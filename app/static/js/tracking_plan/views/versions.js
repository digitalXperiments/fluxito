// app/static/js/tracking_plan/views/versions.js
//
// Published versions list + version-to-version compare. Design system: refined
// to the approved mockup (Flux - TP Versions.dc.html) — kicker/h1/lede header,
// then the version list in a .tp-card; each row is a .tp-ver-row (mono version
// number badge, changelog, mono timestamp, base/head compare pick controls).
// Compare renders the shared .tp-diff change-list inside its own card.
import { h, mount } from "tp/render";
import * as state from "tp/state";
import * as api from "tp/api";
import { groupDiff } from "tp/util/diff";
import { relativeTime } from "tp/util/format";
import { diffSnapshots } from "tp/util/snapshot_diff";
import { renderChangeList } from "./_changelist.js";

let compareSel = { base: null, head: null }; // version ids selected for compare

// mountView: sync, returns cleanup fn. No TDZ: paint is a hoisted function
// declaration, subscribe is called after it is defined.
export function mountView(container) {
  const unsub = state.subscribe(() => paint(container));
  paint(container);
  return () => { unsub(); };
}

async function paint(container) {
  // Kicker + serif h1 + lede (design: TP Versions header) — same treatment as
  // the Review screen's .tp-review-kicker/.tp-review-h1/.tp-review-lede.
  const wrap = h("div", { class: "tp-versions" },
    h("div", { style: { marginBottom: "6px" } },
      h("div", { class: "tp-review-kicker" }, "Versions"),
      h("h1", { class: "tp-review-h1" }, "Every change, ", h("em", {}, "versioned.")),
      h("p", { class: "tp-review-lede" }, "Each publish freezes an immutable snapshot. Pick two versions below to compare.")));
  mount(container, wrap);

  let versions = [];
  try { ({ versions = [] } = await api.versions()); }
  catch (e) { wrap.appendChild(h("div", { class: "tp-empty" }, `Could not load versions: ${e.message || e}`)); return; }

  if (!versions.length) {
    wrap.appendChild(h("div", { class: "tp-row-empty" }, "Nothing published yet. Publish from the main branch."));
    return;
  }

  const list = h("div", { class: "tp-version-list" });
  // Draft-in-review row on top (design: TP Versions timeline shows the open
  // draft above the live version). Only the branch name is real data — there's
  // no "would-be version number" until it's actually merged, so we don't
  // fabricate one.
  const draftBranchName = state.getState().reviewTargetBranch;
  if (draftBranchName) list.appendChild(draftRow(draftBranchName));
  versions.forEach((v, i) => list.appendChild(versionRow(v, container, versions, i === 0)));
  wrap.appendChild(h("div", { class: "tp-card" },
    h("div", { class: "tp-card-h" },
      h("h3", {}, "Versions"),
      h("span", { class: "tp-ct" }, String(versions.length))),
    h("div", { class: "tp-card-b" }, list)));

  const out = h("div", { id: "tp-version-compare" });
  wrap.appendChild(out);
  if (compareSel.base && compareSel.head) renderCompare(out, compareSel.base, compareSel.head, versions);
}

function versionRow(v, container, versions, isLive) {
  const baseChecked = compareSel.base === v.id;
  const headChecked = compareSel.head === v.id;
  // Whole-row wash while this version is part of the active compare (design:
  // TP Versions timeline — matches the exact rgba the mockup uses).
  const isComparing = baseChecked || headChecked;
  const row = h("div", { class: "tp-ver-row" + (isComparing ? " tp-ver-row-comparing" : "") },
    h("div", { class: "tp-ver-num" }, v.version_number),
    h("div", { class: "tp-ver-main" },
      h("div", {},
        v.changelog || "—",
        isLive ? h("span", { class: "tp-ver-badge live" }, "LIVE") : null),
      h("div", { class: "tp-ver-when" }, relativeTime(v.published_at))),
    comparePickBtn("base", "▲", v, baseChecked, container, versions),
    comparePickBtn("head", "▼", v, headChecked, container, versions));
  return row;
}

// Mono "COMPARING ▲/▼" pick control (design: TP Versions timeline) — a real
// radio input (still base/head pairing under the hood) styled as the design's
// link/glyph pattern instead of a visible native radio button.
function comparePickBtn(side, glyph, v, checked, container, versions) {
  const input = h("input", {
    type: "radio", name: `cmp-${side}`, style: { display: "none" }, ...(checked ? { checked: "checked" } : {}),
  });
  input.addEventListener("change", () => {
    compareSel[side] = v.id;
    const out = document.getElementById("tp-version-compare");
    if (compareSel.base && compareSel.head && out) renderCompare(out, compareSel.base, compareSel.head, versions);
  });
  const label = h("label", { class: "tp-ver-pickbtn" + (checked ? " is-active" : "") },
    input, checked ? `COMPARING ${glyph}` : side.toUpperCase());
  return label;
}

// Draft-in-review row (design: TP Versions timeline) — links into the Review
// view for the open draft branch. Not a real "version" (nothing published
// yet), so it has no version number, base/head compare radios, or id.
function draftRow(branchName) {
  return h("div", { class: "tp-ver-row tp-ver-row-draft" },
    h("div", { class: "tp-ver-num tp-ver-num-draft" }, "—"),
    h("div", { class: "tp-ver-main" },
      h("div", {},
        `Draft — ${branchName}`,
        h("span", { class: "tp-ver-badge draft" }, "IN REVIEW")),
      h("div", { class: "tp-ver-when" }, "Proposed by Flux · awaiting review")),
    h("a", {
      class: "tp-link",
      href: "#",
      onClick: (e) => { e.preventDefault(); state.setView("review"); },
    }, "Open review →"));
}

async function renderCompare(out, baseId, headId, versions) {
  out.replaceChildren(h("div", { class: "tp-card" },
    h("div", { class: "tp-card-b" }, h("div", { class: "tp-muted" }, "Loading compare…"))));
  let baseV, headV;
  try {
    [baseV, headV] = await Promise.all([api.version(baseId), api.version(headId)]);
  } catch (e) {
    out.replaceChildren(h("div", { class: "tp-empty" }, `Compare failed: ${e.message || e}`));
    return;
  }

  const diffResp = diffSnapshots(baseV.snapshot, headV.snapshot);
  const grouped = groupDiff(diffResp);

  const card = h("div", { class: "tp-card" },
    h("div", { class: "tp-card-h" },
      h("h3", {}, "Compare"),
      h("span", { class: "tp-ct" },
        h("span", { class: "tp-mono" }, `v${baseV.version_number}`),
        " → ",
        h("span", { class: "tp-mono" }, `v${headV.version_number}`))),
    // SAME renderer as Branch review — no comments here (snapshots are immutable).
    h("div", { class: "tp-card-b" }, renderChangeList(grouped, { summary: diffResp.summary })));
  out.replaceChildren(card);
  out.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
