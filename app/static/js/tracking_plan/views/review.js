// app/static/js/tracking_plan/views/review.js
// Review screen — rebuilt to the approved mockup ("Flux - TP Review.dc.html"):
// a Flux recommendation card, per-change cards tagged +ADD / ~RENAME / ~CHANGE /
// -DEPRECATE, each independently pending|accepted|rejected, and a dark publish
// bar with live counts.
//
// ---------------------------------------------------------------------------
// HOW THE REVIEW STATE MACHINE IS PERSISTED (read this before changing flow)
// ---------------------------------------------------------------------------
// The backend's branch model has no per-change accept/reject concept: a branch
// is diffed as a whole (GET /diff) and merged as a whole (merge_branch, which
// squashes the entire branch into a new published version). There is no
// endpoint to publish a subset of a branch's changes.
//
// So the pending|accepted|rejected decision for EACH change is tracked
// entirely client-side, in tp/state (state.reviewDecisions), keyed by branch
// name + a stable change key (tp/util/diff.changeKey). "Save draft" persists
// those decisions to localStorage (keyed by branch id) purely so a reload
// doesn't lose your review progress — it is NOT sent to the server.
//
// Publishing is gated on every change being resolved (accepted or rejected),
// matching the design's enabled-state rule for the Publish button — but the
// actual publish call is the EXISTING merge_branch action, which merges the
// WHOLE branch. In other words: rejecting a change marks it as excluded in
// the UI and blocks nothing from being included in the merge — there is no
// backend support today for dropping a single rejected change from a branch
// before merge. This is a known, documented limitation of this pass; doing
// selective/partial branch merges would require real backend surgery
// (per-change apply, not just per-branch) that's out of scope here.
import { h, mount } from "tp/render";
import * as state from "tp/state";
import * as api from "tp/api";
import { groupDiff, changeKey } from "tp/util/diff";

const BASE_BRANCH = "main";
const DRAFT_KEY_PREFIX = "tp-review-draft:";
// Branches whose saved localStorage draft has already been restored into
// state this session — restoreDraft is a no-op after the first call per
// branch, both to avoid clobbering in-session choices AND to avoid the
// notify → re-render → reload loop a repeated restore would cause (paint()
// re-enters loadAndPaint() on every state change while this view is mounted).
const restoredBranches = new Set();
// Cache the loaded diff's flattened change list per branch name — accept/
// reject/undo clicks only flip a decision in state (which re-renders this
// view), they never change the diff itself, so there's no need to refetch on
// every click. Invalidated on branch switch (different name) or publish.
let cachedBranchName = null;
let cachedChanges = null;

export function mountView(container) {
  function render() { paint(container); }
  const unsub = state.subscribe(render);
  render();
  return () => { unsub(); };
}

function targetBranch(st) {
  // Prefer the branch the user is actually on if it's a feature branch;
  // otherwise fall back to whatever state.reload() found as the review target
  // (the first non-main branch) — this is how the rail can show a pending
  // count and land here even while sitting on main.
  const cur = (st.branches || []).find((b) => b.name === st.branch);
  if (cur && !cur.is_main) return cur;
  const name = st.reviewTargetBranch;
  if (name) return (st.branches || []).find((b) => b.name === name) || null;
  return null;
}

function paint(container) {
  const st = state.getState();
  const branch = targetBranch(st);

  if (!branch) {
    mount(container, h("div", { class: "tp-review-screen" },
      h("div", { class: "tp-review-scroll" },
        h("div", { class: "tp-review-inner" },
          h("div", { class: "tp-review-kicker" }, "Review"),
          h("h1", { class: "tp-review-h1" }, "Nothing to ", h("em", {}, "review.")),
          h("p", { class: "tp-review-lede" },
            "There's no draft branch with pending changes. Flux opens one here automatically after a drift scan finds something to propose, or you can start one from the branch switcher."),
        ))));
    return;
  }

  if (cachedBranchName === branch.name && cachedChanges) {
    paintScreen(container, branch, cachedChanges);
    return;
  }

  loadAndPaint(container, branch);
}

async function loadAndPaint(container, branch) {
  let diffResp;
  try {
    diffResp = await api.diff(branch.name, BASE_BRANCH);
  } catch (e) {
    mount(container, h("div", { class: "tp-empty" }, "Could not load changes: " + (e.message || String(e))));
    return;
  }

  restoreDraft(branch.name);

  const grouped = groupDiff(diffResp);
  const changes = grouped.flatMap((g) => g.changes);
  cachedBranchName = branch.name;
  cachedChanges = changes;
  paintScreen(container, branch, changes);
}

function paintScreen(container, branch, changes) {
  const decisions = state.getReviewDecisions(branch.name);
  const counts = tallyDecisions(changes, decisions);

  const inner = h("div", { class: "tp-review-inner" },
    h("div", { class: "tp-review-kicker" }, `Review · ${branch.name}`),
    h("h1", { class: "tp-review-h1" },
      "Flux proposes ", h("em", {}, `${changes.length} change${changes.length === 1 ? "" : "s"}.`)),
    h("p", { class: "tp-review-lede" },
      "From the latest diff against main. Accept or reject each — accepted changes are what publishes when you're done."),
    fluxSummaryCard(changes, counts),
    h("div", { class: "tp-review-cards" },
      ...(changes.length
        ? changes.map((c) => changeCard(branch, c, decisions[changeKey(c)] || "pending"))
        : [h("div", { class: "tp-empty" }, "No differences between this branch and main.")])));

  const screen = h("div", { class: "tp-review-screen" },
    h("div", { class: "tp-review-scroll" }, inner, publishBar(branch, changes, counts)));
  mount(container, screen);
}

function tallyDecisions(changes, decisions) {
  let accepted = 0; let rejected = 0;
  for (const c of changes) {
    const d = decisions[changeKey(c)];
    if (d === "accepted") accepted += 1;
    else if (d === "rejected") rejected += 1;
  }
  return { accepted, rejected, pending: changes.length - accepted - rejected, total: changes.length };
}

// Real names, not just tallies (design: TP Review — Flux's card names each
// change). We don't have per-change trade-off reasoning (that's fabricated
// narrative in the mockup), so this stays a specific-but-honest summary: it
// names the actual changed entities and gives the same "review each below"
// steer as before.
function listNames(list) {
  const names = list.slice(0, 3).map((c) => `"${c.name}"`);
  const extra = list.length - names.length;
  return names.join(", ") + (extra > 0 ? ` +${extra} more` : "");
}

function fluxSummaryCard(changes, counts) {
  const parts = [];
  if (counts.total === 0) {
    parts.push("No differences to review right now.");
  } else {
    const adds = changes.filter((c) => c.marker === "+");
    const chgs = changes.filter((c) => c.marker === "~");
    const rems = changes.filter((c) => c.marker === "-");
    const bits = [];
    if (adds.length) bits.push(`adopt ${listNames(adds)}`);
    if (chgs.length) bits.push(`review the update${chgs.length === 1 ? "" : "s"} to ${listNames(chgs)}`);
    if (rems.length) bits.push(`confirm the removal of ${listNames(rems)}`);
    parts.push(`My read: ${bits.join("; ")}. `);
    parts.push("Accept the ones you want and reject the rest — publishing merges the whole branch into main as a new version.");
  }
  return h("div", { class: "tp-review-flux-card" },
    h("span", { class: "tp-review-flux-mark" }, "F"),
    h("div", { class: "tp-review-flux-text" }, parts.join("")));
}

// ---- entity/description helpers (derived from real diff data, not fabricated) ----
function tagFor(c) {
  if (c.marker === "+") return { label: "+ ADD", cls: "add" };
  if (c.marker === "-") return { label: "− DEPRECATE", cls: "rem" };
  const isRename = (c.fields || []).some((f) => f.key === "name");
  return isRename ? { label: "~ RENAME", cls: "chg" } : { label: "~ CHANGE", cls: "chg" };
}

// Returns an array of text/node children for the card body — mixes the
// generic sentence with a real inline-code metadata chip (design: TP Review
// card body — e.g. `string · "paypal" | "stripe" | "apple_pay"`), sourced
// from tp/util/diff's real, derived c.description (never fabricated).
function describe(c) {
  const kind = c.entityType || "item";
  if (c.marker === "+") {
    const parts = [`New ${kind} — adopt it into the plan.`];
    if (c.description) parts.push(" ", h("span", { class: "tp-inlinecode" }, c.description));
    return parts;
  }
  if (c.marker === "-") {
    const parts = ["No longer present in the diff base — remove it from the plan."];
    if (c.description) parts.push(" ", h("span", { class: "tp-inlinecode" }, c.description));
    return parts;
  }
  const nameField = (c.fields || []).find((f) => f.key === "name");
  if (nameField) return [`Rename: ${nameField.was} → ${nameField.now}.`];
  const others = (c.fields || []).filter((f) => f.key !== "name");
  if (!others.length) return [`${kind} changed.`];
  return ["Changed ", h("span", { class: "tp-inlinecode" }, others.map((f) => `${f.key}: ${f.was} → ${f.now}`).join(", ")), "."];
}

function changeCard(branch, c, decision) {
  const tag = tagFor(c);
  const stateLabel = decision === "accepted" ? "✓ ACCEPTED" : decision === "rejected" ? "✗ REJECTED" : "PENDING";

  const head = h("div", { class: "tp-review-card-head" },
    h("span", { class: "tp-review-tag " + tag.cls }, tag.label),
    h("span", { class: "tp-review-card-name" }, String(c.name)),
    h("span", { class: "tp-review-card-state " + decision }, stateLabel));

  const body = h("div", { class: "tp-review-card-body" }, ...describe(c));

  const actions = h("div", { class: "tp-review-card-actions" });
  const key = changeKey(c);
  if (decision === "pending") {
    actions.appendChild(h("button", {
      class: "tp-review-accept",
      onClick: () => decide(branch, key, "accepted"),
    }, "Accept"));
    actions.appendChild(h("button", {
      class: "tp-review-reject",
      onClick: () => decide(branch, key, "rejected"),
    }, "Reject"));
    actions.appendChild(h("a", {
      class: "tp-review-discuss",
      href: "/ask?q=" + encodeURIComponent(`Tell me more about the "${c.name}" ${c.entityType} change on branch "${branch.name}".`),
    }, "Discuss with Flux"));
  } else if (decision === "accepted") {
    actions.appendChild(h("span", { class: "tp-review-chip accepted" }, "Accepted"));
    actions.appendChild(h("button", { class: "tp-review-undo", onClick: () => decide(branch, key, null) }, "Undo"));
  } else {
    actions.appendChild(h("span", { class: "tp-review-chip rejected" }, "Rejected"));
    actions.appendChild(h("button", { class: "tp-review-undo", onClick: () => decide(branch, key, null) }, "Undo"));
  }

  return h("div", { class: "tp-review-card" }, head, body, actions);
}

function decide(branch, key, decision) {
  state.setReviewDecision(branch.name, key, decision);
}

function publishBar(branch, changes, counts) {
  const canPublish = counts.total > 0 && counts.pending === 0;
  return h("div", { class: "tp-review-publishbar" },
    h("div", { style: { flex: "1" } },
      h("div", { class: "tp-review-publish-title" }, `Publish ${branch.name} → ${BASE_BRANCH}`),
      h("div", { class: "tp-review-publish-counts" },
        `${counts.accepted} ACCEPTED · ${counts.rejected} REJECTED · ${counts.pending} PENDING`)),
    h("button", { class: "tp-review-savedraft", onClick: () => saveDraft(branch.name) }, "Save draft"),
    h("button", {
      class: "tp-review-publish",
      disabled: canPublish ? undefined : "disabled",
      onClick: canPublish ? () => doPublish(branch) : undefined,
    }, "Publish"));
}

async function doPublish(branch) {
  const note = window.prompt("Changelog for this version:", `Merged ${branch.name}`);
  if (note === null) return;
  try {
    const r = await api.doAction("merge_branch", { branch_id: branch.id, changelog: note }, branch.name);
    clearDraft(branch.name);
    state.clearReviewDecisions(branch.name);
    if (cachedBranchName === branch.name) { cachedBranchName = null; cachedChanges = null; }
    banner(`Merged → published ${r.version_number || ""}.`, "ok");
    state.setBranch(BASE_BRANCH);
    state.setView("versions");
    await state.reload();
  } catch (e) {
    banner((e.errorType || "error") + ": " + e.message, "err");
  }
}

// ---- localStorage "Save draft" (UI-only persistence of the review decisions) ----
function draftStorageKey(branchName) { return DRAFT_KEY_PREFIX + branchName; }

function saveDraft(branchName) {
  try {
    const decisions = state.getReviewDecisions(branchName);
    window.localStorage.setItem(draftStorageKey(branchName), JSON.stringify(decisions));
    banner("Draft saved on this device.", "ok");
  } catch {
    banner("Could not save draft (storage unavailable).", "err");
  }
}

function restoreDraft(branchName) {
  if (restoredBranches.has(branchName)) return;
  restoredBranches.add(branchName);
  // Only restore into a branch that has no in-memory decisions yet, so we never
  // clobber choices the user already made this session.
  if (Object.keys(state.getReviewDecisions(branchName)).length) return;
  try {
    const raw = window.localStorage.getItem(draftStorageKey(branchName));
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (saved && Object.keys(saved).length) state.setReviewDecisions(branchName, saved);
  } catch { /* corrupt/missing draft — ignore */ }
}

function clearDraft(branchName) {
  restoredBranches.delete(branchName);
  try { window.localStorage.removeItem(draftStorageKey(branchName)); } catch { /* ignore */ }
}

function banner(msg, kind) {
  const b = document.getElementById("tp-banner");
  if (!b) {
    if (kind === "err") console.error(msg);
    return;
  }
  b.className = "tp-banner " + (kind || "ok");
  b.style.display = "flex";
  b.textContent = msg;
  if (kind !== "err") {
    setTimeout(() => { b.style.display = "none"; b.textContent = ""; }, 3200);
  }
}
