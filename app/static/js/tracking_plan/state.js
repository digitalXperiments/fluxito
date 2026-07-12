// app/static/js/tracking_plan/state.js
// Single source of truth for the rendered UI. Views read getState().plan and
// re-render on every change. Every successful write calls reload(), which
// re-fetches the plan for the current branch — so a create can never leave the
// list stale (the headline-bug structural fix).
//
// SAVE MODEL: editors now own save (explicit buffered-save via tp/util/editor).
// State no longer drives a global autosave indicator. We keep `dirty` purely as
// the navigation guard signal — the shell reads state.getState().dirty to decide
// whether to confirm('Discard unsaved changes?') before switching entity/view.
// beginSave/endSave are retained as no-op-safe stubs only because util/persist
// still imports them; they have no UI effect anymore.

import * as api from 'tp/api';
import { groupDiff, changeKey } from 'tp/util/diff';

const _state = {
  branch: 'main',
  plan: null,
  branches: [],
  vendors: null, // { destinations:[{slug,display_name,category,source}], source_platforms:[{slug,display_name}] }
  view: 'events',
  selection: { type: null, id: null },
  dirty: false, // editor has unsaved draft changes — drives the nav guard only
  // ---- Review (per-change accept/reject) state machine — CLIENT-SIDE ONLY ----
  // The backend has no per-change accept/reject model: a branch is merged/
  // published as a whole (merge_branch). So the Review screen (views/review.js)
  // tracks each diff change's pending|accepted|rejected decision here, keyed by
  // branch name, and only persists the *final* publish via the existing
  // merge_branch action. See views/review.js for the full explanation.
  //   reviewDecisions: { [branchName]: { [changeKey]: 'accepted' | 'rejected' } }
  reviewDecisions: {},
  // The branch currently in review (first non-main branch found), and its live
  // pending-change count — kept in sync by reload() so the rail's "Review" pill
  // (shell.js) can render synchronously without its own fetch.
  reviewTargetBranch: null,
  reviewPendingCount: 0,
};
const _subs = new Set();

export function initState(dataset) {
  _state.uid = dataset.uid || '';
  _state.isAdmin = dataset.admin === 'true';
}

export function getState() { return _state; }
export function isAdmin() { return !!_state.isAdmin; }
export function myId() { return _state.uid; }

function _notify() { for (const fn of _subs) fn(_state); }

export function subscribe(fn) {
  _subs.add(fn);
  return () => _subs.delete(fn);
}

export function setPlan(plan) { _state.plan = plan; _notify(); }
export function setBranches(list) { _state.branches = list; _notify(); }

export function setBranch(b) {
  _state.branch = b;
  _state.selection = { type: null, id: null };
  _state.dirty = false;
  // switching branch implies a fresh plan; caller awaits reload().
}

export function setView(name) {
  _state.view = name;
  _state.selection = { type: null, id: null };
  _state.dirty = false;
  _notify();
}

export function select(type, id) {
  _state.selection = { type, id };
  _notify();
}

// Editors set this true when their draft diverges from server, false on
// save/discard. The shell guards entity/view navigation on it.
export function setDirty(b) { const v = !!b; if (_state.dirty === v) return; _state.dirty = v; _notify(); }

// ---- Review decisions (client-side per-change pending|accepted|rejected) ----
export function getReviewDecisions(branchName) {
  return _state.reviewDecisions[branchName] || {};
}

// decision: 'accepted' | 'rejected' | null (null/undefined clears back to pending)
export function setReviewDecision(branchName, changeKey, decision) {
  const bucket = { ..._state.reviewDecisions[branchName] };
  if (decision) bucket[changeKey] = decision;
  else delete bucket[changeKey];
  _state.reviewDecisions = { ..._state.reviewDecisions, [branchName]: bucket };
  _notify();
}

// Bulk-replace a branch's decisions in one notify (used to restore a saved
// draft from localStorage without triggering a re-render per entry).
export function setReviewDecisions(branchName, decisions) {
  _state.reviewDecisions = { ..._state.reviewDecisions, [branchName]: { ...decisions } };
  _notify();
}

// Called after a successful publish/merge — the branch's decisions no longer apply.
export function clearReviewDecisions(branchName) {
  if (!_state.reviewDecisions[branchName]) return;
  const next = { ..._state.reviewDecisions };
  delete next[branchName];
  _state.reviewDecisions = next;
  _notify();
}

// ---- legacy save-status stubs ----
// Editors own save now (tp/util/editor); these remain only so util/persist's
// imports keep resolving. They are no-op-safe and drive no UI.
export function beginSave() {}
export function endSave() {}

// Re-fetch the plan (+ branch list) for the current branch and publish it.
// Also fetches the vendor catalog once (cached for the session).
export async function reload() {
  const plan = await api.getPlan(_state.branch);
  _state.plan = plan;
  const fetches = [];
  fetches.push(
    api.branches()
      .then((lb) => { _state.branches = lb.branches || []; })
      .catch(() => {
        if (!_state.branches.length) _state.branches = [{ name: 'main', is_main: true }];
      }),
  );
  if (!_state.vendors) {
    fetches.push(
      api.vendors()
        .then((v) => { _state.vendors = v; })
        .catch(() => { /* catalog unavailable — views fall back to empty arrays */ }),
    );
  }
  await Promise.all(fetches);
  await refreshReviewCount();
  _notify();
  return plan;
}

// Recompute reviewTargetBranch + reviewPendingCount for the rail's Review pill.
// Picks the current branch if it's a feature branch, else the first non-main
// branch in the list (there is normally at most one active draft). Fetches its
// diff-vs-main and counts changes not yet accepted/rejected client-side.
async function refreshReviewCount() {
  const list = _state.branches || [];
  const cur = list.find((b) => b.name === _state.branch);
  const target = (cur && !cur.is_main) ? cur : list.find((b) => !b.is_main) || null;
  if (!target) {
    _state.reviewTargetBranch = null;
    _state.reviewPendingCount = 0;
    return;
  }
  _state.reviewTargetBranch = target.name;
  try {
    const diffResp = await api.diff(target.name, 'main');
    const grouped = groupDiff(diffResp);
    const decisions = _state.reviewDecisions[target.name] || {};
    let pending = 0;
    for (const g of grouped) {
      for (const c of g.changes) {
        if (!decisions[changeKey(c)]) pending += 1;
      }
    }
    _state.reviewPendingCount = pending;
  } catch {
    _state.reviewPendingCount = 0;
  }
}
