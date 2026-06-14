// app/static/js/tracking_plan/shell.js
// Workspace shell: nav rail (top: branch switcher + review pill), top action bar.
// Subscribes to state; calls setView/setBranch. Hosts the view container.

import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { getPid } from 'tp/api';
import { titleCase, relativeTime } from 'tp/util/format';
import { hasRetry, retryLast } from 'tp/util/persist';

const NAV = [
  ['overview', 'Overview'],
  ['events', 'Events'],
  ['properties', 'Properties'],
  ['bundles', 'Bundles'],
  ['categories', 'Categories'],
  ['sources', 'Sources & Destinations'],
  ['metrics', 'Metrics'],
];

export function mountShell(root) {
  root.classList.add('tp-workspace');
  const rail = h('aside', { class: 'tp-nav' });
  const top = h('div', { class: 'tp-topbar' });
  const viewHost = h('div', { class: 'tp-viewhost', id: 'tp-viewhost' });
  const body = h('div', { class: 'tp-ws-body' }, rail, h('div', { class: 'tp-ws-main' }, top, viewHost));
  mountAll(root, [body]);

  const renderChrome = () => { renderRail(rail); renderTop(top); };
  state.subscribe(renderChrome);
  renderChrome();
  return viewHost;
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
    onClick: () => state.setView(key),
  }, label));

  // Branch review entry (non-main only)
  if (!onMain) {
    items.push(h('button', {
      class: 'tp-nav-item tp-nav-review' + (s.view === 'review' ? ' is-active' : ''),
      onClick: () => state.setView('review'),
    }, 'Branch review'));
  }
  items.push(h('button', {
    class: 'tp-nav-item' + (s.view === 'versions' ? ' is-active' : ''),
    onClick: () => state.setView('versions'),
  }, 'Versions'));

  mountAll(rail, [switcher, pill, h('div', { class: 'tp-nav-list' }, ...items)].filter(Boolean));
}

function renderTop(top) {
  const s = state.getState();
  const b = curBranch();
  const onMain = b.is_main || s.branch === 'main';
  const base = `/api/projects/${getPid()}/tracking-plan`;
  const qs = s.branch && s.branch !== 'main' ? `?branch=${encodeURIComponent(s.branch)}` : '';

  const acts = [
    h('button', { class: 'btn btn-ghost btn-sm', onClick: validate }, 'Validate'),
    h('a', { class: 'btn btn-ghost btn-sm', href: `${base}/export.md${qs}`, target: '_blank' }, '.md'),
    h('a', { class: 'btn btn-ghost btn-sm', href: `${base}/export.xlsx${qs}`, target: '_blank' }, '.xlsx'),
  ];
  if (onMain) {
    acts.push(h('button', { class: 'btn btn-primary btn-sm', onClick: publish }, 'Publish'));
  } else {
    const rs = b.review_status || 'draft';
    if (rs === 'draft') acts.push(h('button', { class: 'btn btn-secondary btn-sm', onClick: () => setReview(b, 'ready_for_review') }, 'Request review'));
    if (rs === 'ready_for_review' && state.isAdmin()) {
      acts.push(h('button', { class: 'btn btn-secondary btn-sm', onClick: () => setReview(b, 'approved') }, 'Approve'));
      acts.push(h('button', { class: 'btn btn-ghost btn-sm', onClick: () => setReview(b, 'changes_requested') }, 'Request changes'));
    }
    if (state.isAdmin()) acts.push(h('button', { class: 'btn btn-primary btn-sm', onClick: () => mergeBranch(b) }, 'Merge & publish'));
  }
  mountAll(top, [renderSaveState(s), h('div', { class: 'tp-topbar-actions' }, ...acts)]);
}

// Persistent save-state indicator (left of the topbar action buttons). Reflects
// the state save-status lifecycle; re-rendered on every notify via renderTop.
function renderSaveState(s) {
  const st = s.saveStatus;
  const box = h('div', { class: 'tp-savestate', dataset: { s: st } });
  if (st === 'saving') {
    box.appendChild(h('span', {}, '⟳ Saving…'));
  } else if (st === 'saved') {
    const rel = s.savedAt ? relativeTime(s.savedAt) : '';
    box.appendChild(h('span', {}, '✓ All changes saved' + (rel ? ' · ' + rel : '')));
  } else if (st === 'error') {
    box.appendChild(h('span', {}, '⚠ Couldn’t save'));
    if (hasRetry()) {
      box.appendChild(h('button', { class: 'btn btn-ghost btn-sm tp-savestate-retry', onClick: () => { retryLast().catch(() => {}); } }, 'Retry'));
    } else {
      box.appendChild(h('span', {}, ' — change again to retry'));
    }
  } else if (s.plan) {
    // idle, once a plan is loaded: a muted resting state.
    box.appendChild(h('span', { class: 'tp-savestate-muted' }, 'All changes saved'));
  }
  return box;
}

// ---- shell actions ----
async function validate() {
  try {
    const r = await api.validate(state.getState().branch);
    const warns = (r.findings || []).filter((f) => f.severity === 'warning').length;
    banner(`${(r.findings || []).length} findings · ${warns} warnings · ${r.is_publishable ? 'publishable' : 'resolve warnings first'}`, warns ? 'warn' : 'ok');
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
