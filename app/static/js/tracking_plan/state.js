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

const _state = {
  branch: 'main',
  plan: null,
  branches: [],
  view: 'overview',
  selection: { type: null, id: null },
  dirty: false, // editor has unsaved draft changes — drives the nav guard only
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
export function setDirty(b) { _state.dirty = !!b; _notify(); }

// ---- legacy save-status stubs ----
// Editors own save now (tp/util/editor); these remain only so util/persist's
// imports keep resolving. They are no-op-safe and drive no UI.
export function beginSave() {}
export function endSave() {}

// Re-fetch the plan (+ branch list) for the current branch and publish it.
export async function reload() {
  const plan = await api.getPlan(_state.branch);
  _state.plan = plan;
  try {
    const lb = await api.branches();
    _state.branches = lb.branches || [];
  } catch (e) {
    _state.branches = _state.branches.length ? _state.branches : [{ name: 'main', is_main: true }];
  }
  _notify();
  return plan;
}
