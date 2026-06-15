// app/static/js/tracking_plan/shell.js
// Workspace shell: nav rail (top: branch switcher + review pill), top action bar.
// Subscribes to state; calls setView/setBranch. Hosts the view container.
//
// TOPBAR (redesign): left = breadcrumb (.tp-crumb) showing 'Events / <current>'
// or just the view name; right = .tp-topbar-actions with the GLOBAL plan actions
// (Validate, Export ▾ → .md/.xlsx, Publish / branch review+merge). There is no
// global save-state indicator anymore — save is per-editor (tp/util/editor).

import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { getPid } from 'tp/api';
import { titleCase } from 'tp/util/format';

const NAV = [
  ['overview', 'Overview'],
  ['events', 'Events'],
  ['properties', 'Properties'],
  ['bundles', 'Bundles'],
  ['categories', 'Categories'],
  ['sources', 'Sources & Destinations'],
  ['metrics', 'Metrics'],
  ['issues', 'Issues'],
];

// view key → breadcrumb label + the plan collection holding its entities (for
// resolving a selected entity's display name in the crumb).
const VIEW_META = {
  overview: { label: 'Overview' },
  events: { label: 'Events', coll: 'events' },
  properties: { label: 'Properties', coll: 'properties' },
  bundles: { label: 'Bundles', coll: 'bundles' },
  categories: { label: 'Categories', coll: 'categories' },
  sources: { label: 'Sources & Destinations' },
  metrics: { label: 'Metrics', coll: 'metrics' },
  review: { label: 'Branch review' },
  versions: { label: 'Versions' },
  issues: { label: 'Issues' },
};

// Views that use the master-detail drill-down on mobile. `sources` is listed
// for intent but never sets a selection, so it never triggers the class.
const MD_VIEWS = new Set(['events', 'properties', 'categories', 'bundles', 'sources', 'metrics']);
const MOBILE_MQ = '(max-width: 768px)';

export function mountShell(root) {
  root.classList.add('tp-workspace');
  const rail = h('aside', { class: 'tp-nav' });
  const top = h('div', { class: 'tp-topbar' });
  const viewHost = h('div', { class: 'tp-viewhost', id: 'tp-viewhost' });
  const wsMain = h('div', { class: 'tp-ws-main' }, top, viewHost);
  const body = h('div', { class: 'tp-ws-body' }, rail, wsMain);
  mountAll(root, [body]);

  // --- mobile drill-down coordinator (central; no per-view edits) ---
  installDrillDown(viewHost, wsMain);

  const renderChrome = () => { renderRail(rail); renderTop(top); };
  state.subscribe(renderChrome);
  renderChrome();
  return viewHost;
}

// Toggles `tp-detail-open` on #tp-viewhost when, on a small screen, the current
// view is a master-detail view AND an entity is selected. Provides a persistent
// .tp-back control (cleared via state.select(null, null)). Engages only via
// matchMedia so desktop is unaffected, and survives shell re-renders because it
// owns elements the renderers never clear.
function installDrillDown(viewHost, wsMain) {
  const mq = window.matchMedia(MOBILE_MQ);

  // Persistent back button, inserted once as the first child of .tp-ws-main
  // (renderTop/renderRail never touch .tp-ws-main's children directly, and
  // mountAll(top, …) only clears `top`, so this survives every re-render).
  const back = h('button', { class: 'tp-back', type: 'button',
    onClick: () => state.select(null, null) },
    h('span', { 'aria-hidden': 'true' }, '‹'), 'Back to list');
  wsMain.insertBefore(back, wsMain.firstChild);

  const sync = () => {
    const s = state.getState();
    const open = mq.matches && MD_VIEWS.has(s.view) && s.selection != null && s.selection.id != null;
    viewHost.classList.toggle('tp-detail-open', open);
  };

  state.subscribe(sync);               // re-evaluate on every state change
  mq.addEventListener('change', sync); // re-evaluate when crossing the breakpoint
  sync();                              // initial
}

function curBranch() {
  const s = state.getState();
  return (s.branches || []).find((b) => b.name === s.branch || b.id === s.branch)
    || { name: s.branch, is_main: s.branch === 'main' };
}

function renderRail(rail) {
  const s = state.getState();
  const b = curBranch();
  const onMain = b.is_main || s.branch === 'main';

  // branch switcher
  const switcher = h('div', { class: 'tp-branchpick' });
  const btn = h('button', { class: 'tp-branch-btn', onClick: (e) => { e.stopPropagation(); menu.classList.toggle('is-open'); } },
    h('b', {}, b.name),
    h('span', { class: 'tp-caret' }, '▾'));
  const menu = h('div', { class: 'tp-menu' });
  for (const br of s.branches || []) {
    menu.appendChild(h('div', {
      class: 'tp-menu-item' + (br.name === s.branch ? ' is-active' : ''),
      onClick: async () => {
        menu.classList.remove('is-open');
        if (!guardNav()) return;
        state.setBranch(br.name);
        state.setView('overview');
        await state.reload();
      },
    }, h('span', { class: 'tp-mono' }, br.name),
       h('span', { class: 'tp-menu-meta' }, br.is_main ? 'main' : titleCase(br.review_status || br.status || ''))));
  }
  menu.appendChild(h('div', { class: 'tp-menu-sep' }));
  menu.appendChild(h('div', { class: 'tp-menu-item', onClick: () => { menu.classList.remove('is-open'); createBranch(); } }, '+ New branch…'));
  document.addEventListener('click', () => menu.classList.remove('is-open'), { once: true });
  switcher.appendChild(btn);
  switcher.appendChild(menu);

  const pill = onMain ? null
    : h('span', { class: 'tp-review', dataset: { s: b.review_status || 'draft' } }, titleCase(b.review_status || 'draft'));

  // nav items
  const items = NAV.map(([key, label]) => h('button', {
    class: 'tp-nav-item' + (s.view === key ? ' is-active' : ''),
    onClick: () => navTo(key),
  }, label));

  // Branch review entry (non-main only)
  if (!onMain) {
    items.push(h('button', {
      class: 'tp-nav-item tp-nav-review' + (s.view === 'review' ? ' is-active' : ''),
      onClick: () => navTo('review'),
    }, 'Branch review'));
  }
  items.push(h('button', {
    class: 'tp-nav-item' + (s.view === 'versions' ? ' is-active' : ''),
    onClick: () => navTo('versions'),
  }, 'Versions'));

  mountAll(rail, [switcher, pill, h('div', { class: 'tp-nav-list' }, ...items)].filter(Boolean));
}

// ---- navigation guard ----
// If an editor has an unsaved draft (state.dirty), confirm before navigating
// away (switching view, branch, or selecting a different entity). Returns false
// when the user cancels — callers must abort the navigation. Editors clear
// state.dirty on save/discard; selecting WITHIN a view is guarded by the view.
function guardNav() {
  if (!state.getState().dirty) return true;
  // eslint-disable-next-line no-alert
  if (confirm('Discard unsaved changes?')) { state.setDirty(false); return true; }
  return false;
}

function navTo(view) {
  if (view === state.getState().view) return;
  if (!guardNav()) return;
  state.setView(view);
}

// ---- breadcrumb ----
// 'Events / checkout_started' when an entity is selected in a list view, else
// just the view label.
function crumbFor(s) {
  const meta = VIEW_META[s.view] || { label: titleCase(s.view || '') };
  const parts = [h('span', { class: 'tp-crumb-root' }, meta.label)];
  const sel = s.selection || {};
  if (sel.id != null && meta.coll) {
    const raw = (s.plan && s.plan[meta.coll]) || [];
    // properties is an object keyed by kind ({event,user,group,system}); flatten it.
    const list = Array.isArray(raw) ? raw : Object.values(raw).flat();
    const ent = list.find((x) => x.id === sel.id || x.name === sel.id);
    const name = ent ? (ent.name || ent.display_name || String(sel.id)) : String(sel.id);
    parts.push(h('span', { class: 'tp-crumb-sep' }, '/'));
    parts.push(h('b', { class: 'tp-crumb-cur' }, name));
  }
  return h('div', { class: 'tp-crumb' }, ...parts);
}

function renderTop(top) {
  const s = state.getState();
  const b = curBranch();
  const onMain = b.is_main || s.branch === 'main';
  const base = `/api/projects/${getPid()}/tracking-plan`;
  const qs = s.branch && s.branch !== 'main' ? `?branch=${encodeURIComponent(s.branch)}` : '';

  const acts = [
    h('button', { class: 'btn btn-ghost btn-sm', onClick: validate }, 'Validate'),
    exportMenu(base, qs),
  ];
  if (onMain) {
    acts.push(h('button', { class: 'btn btn-primary btn-sm tp-publish', onClick: publish }, 'Publish'));
  } else {
    const rs = b.review_status || 'draft';
    if (rs === 'draft') acts.push(h('button', { class: 'btn btn-secondary btn-sm', onClick: () => setReview(b, 'ready_for_review') }, 'Request review'));
    if (rs === 'ready_for_review' && state.isAdmin()) {
      acts.push(h('button', { class: 'btn btn-secondary btn-sm', onClick: () => setReview(b, 'approved') }, 'Approve'));
      acts.push(h('button', { class: 'btn btn-ghost btn-sm', onClick: () => setReview(b, 'changes_requested') }, 'Request changes'));
    }
    if (state.isAdmin()) acts.push(h('button', { class: 'btn btn-primary btn-sm tp-publish', onClick: () => mergeBranch(b) }, 'Merge & publish'));
  }
  mountAll(top, [crumbFor(s), h('div', { class: 'tp-topbar-actions' }, ...acts)]);
}

// Export ▾ — a small dropdown grouping the two export links (.md / .xlsx).
function exportMenu(base, qs) {
  const wrap = h('div', { class: 'tp-export' });
  const menu = h('div', { class: 'tp-menu tp-export-menu' },
    h('a', { class: 'tp-menu-item', href: `${base}/export.md${qs}`, target: '_blank' }, 'Markdown (.md)'),
    h('a', { class: 'tp-menu-item', href: `${base}/export.xlsx${qs}`, target: '_blank' }, 'Excel (.xlsx)'));
  const btn = h('button', {
    class: 'btn btn-secondary btn-sm',
    onClick: (e) => {
      e.stopPropagation();
      const open = menu.classList.toggle('is-open');
      if (open) document.addEventListener('click', () => menu.classList.remove('is-open'), { once: true });
    },
  }, 'Export ▾');
  wrap.appendChild(btn);
  wrap.appendChild(menu);
  return wrap;
}

// ---- shell actions ----
async function validate() {
  try {
    const r = await api.validate(state.getState().branch);
    const errs = (r.findings || []).filter((f) => f.severity === 'error').length;
    const warns = (r.findings || []).filter((f) => f.severity === 'warning').length;
    banner(
      `${(r.findings || []).length} findings · ${errs} errors · ${warns} warnings · ${r.is_publishable ? 'publishable' : 'resolve errors first'}`,
      errs ? 'err' : (warns ? 'warn' : 'ok'),
    );
    if (!guardNav()) return;
    state.setView('issues');
  } catch (e) { banner(e.message, 'err'); }
}
async function publish() {
  const note = prompt('Changelog for this version:');
  if (note === null) return;
  await act('publish', { changelog: note }, (r) => `Published version ${r.version_number}.`);
}
async function createBranch() {
  const name = prompt('New branch name (e.g. add-checkout-events):'); if (!name) return;
  const description = prompt("What's this branch for? (optional)") || null;
  try {
    const r = await api.doAction('create_branch', { name, description }, state.getState().branch);
    state.setBranch(r.name); state.setView('overview'); await state.reload();
    banner(`Branch "${r.name}" created.`, 'ok');
  } catch (e) { banner(e.message, 'err'); }
}
async function mergeBranch(b) {
  if (!confirm(`Merge "${b.name}" into main and publish a new version?`)) return;
  const changelog = prompt('Changelog for this merge:', `Merged ${b.name}`);
  try {
    const r = await api.doAction('merge_branch', { branch_id: b.id, changelog }, state.getState().branch);
    banner(`Merged → published ${r.version_number}.`, 'ok');
    state.setBranch('main'); state.setView('overview'); await state.reload();
  } catch (e) { banner(e.message, 'err'); }
}
async function setReview(b, review_status) {
  try {
    await api.doAction('set_review_status', { branch_id: b.id, review_status }, state.getState().branch);
    banner(`Branch marked ${titleCase(review_status)}.`, 'ok');
    await state.reload();
  } catch (e) { banner(e.message, 'err'); }
}
async function act(action, params, okMsg) {
  try {
    const r = await api.doAction(action, params, state.getState().branch);
    banner(typeof okMsg === 'function' ? okMsg(r) : okMsg, 'ok');
    await state.reload();
  } catch (e) { banner(e.message, 'err'); }
}

let _bannerTimer;
export function banner(msg, kind = 'ok') {
  const el = document.getElementById('tp-banner');
  if (!el) return;
  if (!msg) { el.style.display = 'none'; el.textContent = ''; return; }
  el.className = 'tp-banner ' + kind; el.style.display = 'flex'; el.textContent = msg;
  clearTimeout(_bannerTimer);
  if (kind === 'ok') _bannerTimer = setTimeout(() => banner(''), 3200);
}
// Expose banner to the drawer (which has no shell import).
window.__tpBanner = banner;
