// app/static/js/tracking_plan/util/persist.js
// Shared write wrapper every editor view uses for save feedback. Drives the
// state save-status lifecycle (beginSave/endSave), toasts on success and shows
// the tracking-plan banner on error. window.Fluxito.toast is the global app
// toast (app.js); window.__tpBanner is the tracking-plan banner (shell.js).

import * as state from 'tp/state';

// Last failed {label, fn}, so the topbar Retry button can re-run it.
let _lastFailed = null;
export function hasRetry() { return _lastFailed !== null; }
export async function retryLast() {
  if (!_lastFailed) return undefined;
  const { label, fn } = _lastFailed;
  return persist(label, fn);
}

// run a write, drive the save-status lifecycle, toast on success, banner on error.
export async function persist(label, fn) {
  state.beginSave();
  try {
    const r = await fn();
    state.endSave(true);
    _lastFailed = null;
    if (label && window.Fluxito && window.Fluxito.toast) window.Fluxito.toast(label, 'success');
    return r;
  } catch (err) {
    state.endSave(false, err && err.message);
    _lastFailed = { label, fn };
    if (window.__tpBanner) window.__tpBanner((err && err.message) || 'Save failed', 'err');
    throw err;
  }
}
