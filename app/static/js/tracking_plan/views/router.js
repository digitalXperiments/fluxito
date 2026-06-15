// app/static/js/tracking_plan/views/router.js
// Maps state.view -> the view module; remounts on view change; tears down the
// previous view (each mountView returns a cleanup fn).

import * as state from 'tp/state';
import * as overview from 'tp/views/overview';
import * as events from 'tp/views/events';
import * as properties from 'tp/views/properties';
import * as categories from 'tp/views/categories';
import * as bundles from 'tp/views/bundles';
import * as sources from 'tp/views/sources';
import * as review from 'tp/views/review';
import * as versions from 'tp/views/versions';
import * as issues from 'tp/views/issues';

const VIEWS = {
  overview: overview.mountView,
  events: events.mountView,
  properties: properties.mountView,
  categories: categories.mountView,
  bundles: bundles.mountView,
  sources: sources.mountView,
  review: review.mountView,
  versions: versions.mountView,
  issues: issues.mountView,
};

export function mountActiveView(host) {
  let current = null;
  let cleanup = null;
  function swap() {
    const v = state.getState().view;
    if (v === current) return;     // only remount when the view actually changes
    current = v;
    if (cleanup) { try { cleanup(); } catch (e) {} cleanup = null; }
    host.replaceChildren();
    const mountView = VIEWS[v] || VIEWS.events;
    cleanup = mountView(host) || null;
  }
  state.subscribe(swap);
  swap();
}
