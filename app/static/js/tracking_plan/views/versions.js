// app/static/js/tracking_plan/views/versions.js
//
// Published versions list + version-to-version compare. Design system: refined
// to the approved mockup. The page lead is a .tp-d-head (sentence-case title +
// muted description); the version list lives in a .tp-card; each row is a
// .tp-ver-row (mono version number badge, changelog, mono timestamp, base/head
// radios). Compare renders the shared .tp-diff change-list inside its own card.
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
  const wrap = h("div", { class: "tp-versions" },
    h("div", { class: "tp-d-head" },
      h("div", { class: "tp-d-title" },
        h("h2", {}, "Published versions"),
        h("div", { class: "tp-muted" }, "Each publish freezes an immutable snapshot. Pick two to compare."))));
  mount(container, wrap);

  let versions = [];
  try { ({ versions = [] } = await api.versions()); }
  catch (e) { wrap.appendChild(h("div", { class: "tp-empty" }, `Could not load versions: ${e.message || e}`)); return; }

  if (!versions.length) {
    wrap.appendChild(h("div", { class: "tp-row-empty" }, "Nothing published yet. Publish from the main branch."));
    return;
  }

  const list = h("div", { class: "tp-version-list" });
  versions.forEach((v) => list.appendChild(versionRow(v, container, versions)));
  wrap.appendChild(h("div", { class: "tp-card" },
    h("div", { class: "tp-card-h" },
      h("h3", {}, "Versions"),
      h("span", { class: "tp-ct" }, String(versions.length))),
    h("div", { class: "tp-card-b" }, list)));

  const out = h("div", { id: "tp-version-compare" });
  wrap.appendChild(out);
  if (compareSel.base && compareSel.head) renderCompare(out, compareSel.base, compareSel.head, versions);
}

function versionRow(v, container, versions) {
  const baseChecked = compareSel.base === v.id;
  const headChecked = compareSel.head === v.id;
  const row = h("div", { class: "tp-ver-row" },
    h("div", { class: "tp-ver-num" }, v.version_number),
    h("div", { class: "tp-ver-main" },
      h("div", {}, v.changelog || "—"),
      h("div", { class: "tp-ver-when" }, relativeTime(v.published_at))),
    h("label", { class: "tp-ver-pick" }, baseRadio("base", v, baseChecked, container, versions), " base"),
    h("label", { class: "tp-ver-pick" }, baseRadio("head", v, headChecked, container, versions), " head"));
  return row;
}

function baseRadio(side, v, checked, container, versions) {
  const r = h("input", { type: "radio", name: `cmp-${side}`, ...(checked ? { checked: "checked" } : {}) });
  r.addEventListener("change", () => {
    compareSel[side] = v.id;
    const out = document.getElementById("tp-version-compare");
    if (compareSel.base && compareSel.head && out) renderCompare(out, compareSel.base, compareSel.head, versions);
  });
  return r;
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
