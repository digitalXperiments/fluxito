// app/static/js/tracking_plan/views/review.js  (pure gating — exported for tests)
// Pure predicates are at the top, before any browser-only imports, so that
// node --test can import this module directly (tests/js/tracking_plan/review_gating.test.mjs).

const ADMIN_ROLES = new Set(["owner", "admin"]);

export function canMerge(reviewStatus, role) {
  return ADMIN_ROLES.has(role) && reviewStatus === "approved";
}

// Returns the ordered review actions to render for the current branch state.
// Each: { id, label, status?, kind } — `status` is the set_review_status target;
// `merge` has no status (it calls merge_branch). `disabled` gates the button.
export function reviewActionsFor(reviewStatus, role, isMain) {
  if (isMain) return [];
  const isAdmin = ADMIN_ROLES.has(role);
  const out = [];
  if (reviewStatus === "draft") {
    out.push({ id: "request_review", label: "Request review", status: "ready_for_review", kind: "secondary" });
  }
  if (reviewStatus === "ready_for_review") {
    out.push({ id: "approve", label: "Approve", status: "approved", kind: "secondary" });
    out.push({ id: "request_changes", label: "Request changes", status: "changes_requested", kind: "ghost" });
  }
  if (reviewStatus === "changes_requested") {
    out.push({ id: "request_review", label: "Re-request review", status: "ready_for_review", kind: "secondary" });
  }
  if (reviewStatus === "approved") {
    out.push({ id: "request_changes", label: "Request changes", status: "changes_requested", kind: "ghost" });
  }
  if (isAdmin && reviewStatus !== "draft") {
    out.push({ id: "merge", label: "Merge & publish", kind: "primary", disabled: !canMerge(reviewStatus, role) });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Browser view (browser-only imports are inside the function to keep the
// module importable from Node for unit-testing the pure predicates above).
// ---------------------------------------------------------------------------
export async function mountView(container) {
  const { h, mountAll } = await import("tp/render");
  const state = await import("tp/state");
  const api = await import("tp/api");
  const { groupDiff } = await import("tp/util/diff");

  const host = h("div", { class: "tp-detail-inner" });
  mountAll(container, [host]);
  load();
  async function load() {
    mountAll(host, [h("div", { class: "tp-muted" }, "Loading changes…")]);
    let diff;
    try { diff = await api.diff(state.getState().branch, "main"); }
    catch (e) { mountAll(host, [h("div", { class: "tp-empty" }, String(e.message || e))]); return; }
    const s = diff.summary || { added: 0, changed: 0, removed: 0 };
    const groups = groupDiff(diff);
    const nodes = [
      h("h2", { style: { margin: "0 0 12px" } }, "Changes vs ", h("span", { class: "tp-mono" }, "main")),
      h("div", { class: "tp-diff-summary" },
        h("span", { class: "tp-diff-stat add" }, `+${s.added} added`),
        h("span", { class: "tp-diff-stat chg" }, `~${s.changed} changed`),
        h("span", { class: "tp-diff-stat rem" }, `−${s.removed} removed`)),
    ];
    if (!groups.length) nodes.push(h("div", { class: "tp-empty" }, "No differences from main yet."));
    for (const g of groups) {
      const grp = h("div", { class: "tp-diff-group" }, h("h3", {}, g.group));
      for (const c of g.changes) {
        const markCls = c.marker === "+" ? "add" : c.marker === "-" ? "rem" : "chg";
        const item = h("div", { class: "tp-diff-item" }, h("span", { class: "tp-diff-mark " + markCls }, c.marker), c.name);
        grp.appendChild(item);
        for (const f of c.fields || []) grp.appendChild(h("div", { class: "tp-diff-field" }, `${f.key}: `, h("span", { class: "tp-was" }, String(f.was ?? "∅")), " → ", h("span", { class: "tp-now" }, String(f.now ?? "∅"))));
      }
      nodes.push(grp);
    }
    mountAll(host, nodes);
  }
  return () => {};
}
