// app/static/js/tracking_plan/views/review.js
// Branch-review screen (§5.10): diff viewer + review panel + merge & publish.
// Pure gating predicates are in tp/util/review_gating (Node-testable separately).

import { h, mount } from "tp/render";
import * as state from "tp/state";
import * as api from "tp/api";
import { groupDiff } from "tp/util/diff";
import { initials, relativeTime } from "tp/util/format";
import { mountDrawer } from "tp/comments";
import { renderChangeList } from "tp/views/_changelist";
import { canMerge, reviewActionsFor } from "tp/util/review_gating";

// Re-export for any legacy import sites (none expected after refactor).
export { canMerge, reviewActionsFor };

const BASE_BRANCH = "main";

// ---------------------------------------------------------------------------
// Contract entry point — SYNC; returns a cleanup function.
// ---------------------------------------------------------------------------
export function mountView(container) {
  let _drawer = null;

  function render() {
    // Destroy any lingering drawer from the previous render cycle.
    if (_drawer) { _drawer.destroy(); _drawer = null; }
    paint(container, (d) => { _drawer = d; });
  }

  const unsub = state.subscribe(render);
  render();

  return () => { unsub(); if (_drawer) _drawer.destroy(); };
}

// ---------------------------------------------------------------------------
// paint — called sync; kicks off async data loads independently.
// ---------------------------------------------------------------------------
function paint(container, onDrawer) {
  const st = state.getState();
  // state.branch is always a branch NAME string (e.g. "main" or "feat/x").
  const branchName = st.branch || BASE_BRANCH;
  // Look up the full branch object from the loaded branches list.
  const branchObj = (st.branches || []).find((b) => b.name === branchName) || null;
  const isMain = !branchName || branchName === BASE_BRANCH ||
    (branchObj && branchObj.is_main);

  // On main: show empty state, no review actions.
  if (isMain) {
    mount(container, h("div", { class: "tp-empty" },
      h("div", {}, "Branch review is available on a feature branch."),
      h("div", { class: "tp-muted" },
        "Create a branch from the switcher to open a pull-request style review.")));
    return;
  }

  // Build shell synchronously so the page isn't blank while data loads.
  // Use the real review_status from the looked-up branch object; fall back to
  // "draft" only when branches haven't loaded yet (branchObj === null).
  const reviewStatus = branchObj?.review_status || "draft";
  const role = resolveRole();
  // Synthetic fallback used only for rendering while branches are still loading.
  const effectiveBranch = branchObj || { name: branchName, review_status: reviewStatus };

  const head = h("div", { class: "tp-review-head" },
    h("div", { class: "tp-review-title" },
      h("span", { class: "tp-mono" }, branchName),
      h("span", { class: "tp-review-arrow" }, "→"),
      h("span", { class: "tp-mono" }, BASE_BRANCH),
      h("span", { class: "tp-review-pill", "data-s": reviewStatus },
        reviewStatus.replace(/_/g, " "))),
    h("div", { class: "tp-review-actions" },
      ...buildActionButtons(effectiveBranch, role)));

  const changesCol = h("div", { class: "tp-review-changes" },
    h("div", { class: "tp-muted" }, "Loading changes…"));
  const panelCol = h("div", { class: "tp-review-panel" });

  mount(container, h("div", { class: "tp-review-screen" },
    head,
    h("div", { class: "tp-review-body" }, changesCol, panelCol)));

  // Async data fills — fire-and-forget; each catches its own errors.
  fillChanges(changesCol, effectiveBranch);
  fillPanel(panelCol, effectiveBranch, onDrawer);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function resolveRole() {
  const el = document.getElementById("tp-app");
  if (el && el.dataset.admin === "true") return "admin";
  return "editor";
}

function buildActionButtons(branch, role) {
  const actions = reviewActionsFor(branch.review_status || "draft", role, !!branch.is_main);
  return actions.map((a) => {
    const attrs = { class: "btn btn-" + a.kind + " btn-sm" };
    if (a.disabled) {
      attrs.disabled = "disabled";
      attrs.title = "Branch must be approved before merging";
    }
    const btn = h("button", attrs, a.label);
    if (!a.disabled) {
      btn.addEventListener("click", () => {
        if (a.id === "merge") {
          doMerge(branch);
        } else {
          doSetReview(branch, a.status);
        }
      });
    }
    return btn;
  });
}

async function doSetReview(branch, status) {
  // Guard: branch.id must be present (real branch object from state.branches).
  if (!branch.id) {
    showBanner("Branch not loaded yet — please try again.", "err");
    return;
  }
  try {
    await api.doAction("set_review_status", { branch_id: branch.id, review_status: status }, branch.name);
    await state.reload();
  } catch (e) {
    showBanner((e.errorType || "error") + ": " + e.message, "err");
  }
}

async function doMerge(branch) {
  // Guard: branch.id must be present (real branch object from state.branches).
  if (!branch.id) {
    showBanner("Branch not loaded yet — please try again.", "err");
    return;
  }
  // merge_branch auto-publishes; its changelog becomes the published version's changelog.
  const note = window.prompt("Changelog for the merged version:", "Merged " + branch.name);
  if (note === null) return; // user cancelled
  try {
    const r = await api.doAction("merge_branch", { branch_id: branch.id, changelog: note }, branch.name);
    showBanner("Merged → published " + (r.version_number || "") + ".", "ok");
    state.setBranch(BASE_BRANCH);
    state.setView("events");
    await state.reload();
  } catch (e) {
    showBanner((e.errorType || "error") + ": " + e.message, "err");
  }
}

async function fillChanges(col, branch) {
  let diffResp;
  try {
    diffResp = await api.diff(branch.name, BASE_BRANCH);
  } catch (e) {
    mount(col, h("div", { class: "tp-empty" },
      "Could not load diff: " + (e.message || String(e))));
    return;
  }

  const grouped = groupDiff(diffResp);
  const counts = await fetchCommentCounts(branch);

  // Replace loading placeholder — use DOM methods, not innerHTML, to avoid XSS.
  col.textContent = "";
  col.appendChild(renderChangeList(grouped, {
    summary: diffResp.summary,
    commentCounts: counts,
    onToggleInline: (change, _rowEl, bodyEl) => {
      const slot = h("div", { class: "tp-inline-comments" });
      bodyEl.appendChild(slot);
      mountDrawer(slot, {
        entityType: change.entityType,
        entityId: change.id,
        branch: branch.name,
      });
    },
  }));
}

async function fetchCommentCounts(branch) {
  try {
    // api.listComments is positional: (entityType, entityId, branch)
    const resp = await api.listComments(null, null, branch.name);
    const comments = resp.comments || [];
    const out = {};
    for (const c of comments) {
      if (!c.entity_type || !c.entity_id) continue;
      const k = c.entity_type + ":" + c.entity_id;
      out[k] = (out[k] || 0) + 1;
    }
    return out;
  } catch {
    return {};
  }
}

async function fillPanel(col, branch, onDrawer) {
  col.textContent = "";

  // --- Reviewers section ---
  const reviewersSection = h("div", { class: "tp-review-section" },
    h("h4", {}, "Reviewers"));

  // --- Activity timeline section ---
  const timelineSection = h("div", { class: "tp-review-section" },
    h("h4", {}, "Activity"));
  const tlBody = h("div", { class: "tp-activity-timeline" },
    h("div", { class: "tp-muted" }, "Loading…"));
  timelineSection.appendChild(tlBody);

  // --- Discussion section ---
  const discussSection = h("div", { class: "tp-review-section" },
    h("h4", {}, "Discussion"));
  const discSlot = h("div", {});
  discussSection.appendChild(discSlot);

  col.appendChild(reviewersSection);
  col.appendChild(timelineSection);
  col.appendChild(discussSection);

  // Mount the general-discussion drawer immediately (branch entity).
  const drawer = mountDrawer(discSlot, {
    entityType: "branch",
    entityId: branch.id || branch.name,
    branch: branch.name,
  });
  if (onDrawer) onDrawer(drawer);

  // Fill reviewers + activity timeline from the branch activity feed.
  try {
    // api.listActivity is positional: (entityType, entityId, branch)
    const resp = await api.listActivity(null, null, branch.name);
    const activity = resp.activity || [];

    tlBody.textContent = "";
    if (!activity.length) {
      tlBody.appendChild(h("div", { class: "tp-muted" }, "No activity yet."));
    }

    const seen = new Set();
    for (const a of activity) {
      tlBody.appendChild(h("div", { class: "tp-activity-row" },
        h("span", { class: "tp-avatar" }, initials(a.actor_id || "?")),
        h("div", { class: "tp-activity-main" },
          h("div", { class: "tp-activity-summary" }, String(a.summary || a.action || "")),
          h("div", { class: "tp-activity-when" }, relativeTime(a.created_at)))));

      // Per-person review status: last set_review_status event per actor.
      if (a.action === "set_review_status" && a.actor_id && !seen.has(a.actor_id)) {
        seen.add(a.actor_id);
        const statusLabel = String(a.summary || "reviewed").replace(/^.*?branch\s*/i, "");
        reviewersSection.appendChild(h("div", { class: "tp-reviewer-row" },
          h("span", { class: "tp-avatar" }, initials(a.actor_id)),
          h("span", { class: "tp-mono" }, (a.actor_id || "").slice(0, 8)),
          h("span", { class: "tp-status", "data-s": "implemented" }, statusLabel)));
      }
    }

    // If no reviewer rows were added, show a placeholder (the h4 is childElementCount === 1).
    if (reviewersSection.childElementCount === 1) {
      reviewersSection.appendChild(h("div", { class: "tp-muted" }, "No review actions yet."));
    }
  } catch {
    tlBody.textContent = "";
    tlBody.appendChild(h("div", { class: "tp-muted" }, "Activity unavailable."));
  }
}

// Lightweight banner — reuses #tp-banner if the TP shell provides it.
function showBanner(msg, kind) {
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
