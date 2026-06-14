// app/static/js/tracking_plan/util/review_gating.js
// Pure predicates for branch-review gating.  No browser-only imports — Node can
// unit-test this module directly (tests/js/tracking_plan/review_gating.test.mjs).

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
