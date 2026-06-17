// app/static/js/tracking_plan/views/categories.js
// Categories — refined master-detail (best-in-class redesign).
// Master: searchable list of categories with a color dot, name (sentence-case
// label) and live event count. Detail: a buffered editor — selecting a category
// snapshots draft={name, color, description}; field edits mutate the DRAFT only
// and re-render; the save cluster commits a single update_category on Save.
// Delete uses the canonical .modal-backdrop confirm (events get uncategorized).
//
// BUFFERED-SAVE MODEL (tp/util/editor): nothing hits the API on edit. dirty is
// !deepEqual(draft, server); the shell guards navigation on state.dirty.

import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { persist } from 'tp/util/persist';
import { clone, isDirty, saveCluster } from 'tp/util/editor';

// Curated swatch palette (identifiers stay mono elsewhere; colors are values).
const SWATCHES = [
  '#4f46e5', '#16a34a', '#d97706', '#dc2626',
  '#7c3aed', '#0891b2', '#db2777', '#65a30d',
];

function countByName(plan) {
  const m = {};
  (plan.events || []).forEach((e) => {
    if (e.category) m[e.category] = (m[e.category] || 0) + 1;
  });
  return m;
}

export function mountView(container) {
  const layout = h('div', { class: 'tp-master-detail' });
  const master = h('div', { class: 'tp-master' });
  const detail = h('div', { class: 'tp-detail', id: 'tp-cat-detail' });
  layout.appendChild(master);
  layout.appendChild(detail);
  mountAll(container, [layout]);

  let search = '';
  // Editor buffer for the selected category.
  let draft = null;     // { name, color, description }
  let server = null;    // snapshot of the same fields from the loaded entity
  let saving = false;
  let editingId = null; // which category the draft belongs to

  // The subscriber always refreshes the (cheap) list. It re-renders the detail
  // only when the focus is NOT inside the detail pane — otherwise a self-notify
  // from setDirty() during typing would blow away the focused input. Structural
  // edits (add/remove/recolor) call renderDetail() directly, so they don't rely
  // on this path.
  const unsub = state.subscribe(() => {
    renderList();
    if (!detail.contains(document.activeElement)) renderDetail();
  });
  renderList();
  renderDetail();

  function plan() { return state.getState().plan; }
  function branch() { return state.getState().branch; }

  function dirty() { return !!(draft && server) && isDirty(draft, server); }

  function setDirtyFlag() { state.setDirty(dirty()); }

  // Snapshot the editable fields of a category into draft + server.
  function snapshot(c) {
    server = { name: c.name || '', color: c.color || '', description: c.description || '' };
    draft = clone(server);
    editingId = c.id;
    state.setDirty(false);
  }

  // ---- master list ----------------------------------------------------------
  function renderList() {
    const head = h('div', { class: 'tp-master-head' },
      h('div', { class: 'tp-search' },
        searchIcon(),
        h('input', {
          class: 'input', placeholder: 'Search categories', value: search,
          onInput: (e) => { search = e.target.value; renderListBody(listBody); },
        })),
      h('button', { class: 'btn btn-primary btn-sm btn-block', onClick: newCategory },
        plusIcon(), 'New category'));
    const listBody = h('div', { class: 'tp-master-list' });
    renderListBody(listBody);
    mountAll(master, [head, listBody]);
  }

  function renderListBody(listBody) {
    if (!plan()) { mountAll(listBody, [h('div', { class: 'tp-row-empty' }, 'Loading…')]); return; }
    const counts = countByName(plan());
    const sel = state.getState().selection;
    let cats = (plan().categories || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    if (search) {
      const q = search.toLowerCase();
      cats = cats.filter((c) => c.name.toLowerCase().includes(q));
    }
    if (!cats.length) {
      mountAll(listBody, [h('div', { class: 'tp-row-empty' },
        search ? 'No matching categories.' : 'No categories yet. Group events with a category.')]);
      return;
    }
    const nodes = cats.map((c) => {
      const n = counts[c.name] || 0;
      return h('div', {
        class: 'tp-ev' + (sel.type === 'category' && sel.id === c.id ? ' is-active' : ''),
        onClick: () => selectCategory(c.id),
      },
        h('span', { class: 'tp-sd', style: { background: c.color || 'var(--tp-text-4)' } }),
        h('div', { class: 'tp-ev-main' },
          // Category names are human labels, not identifiers → sentence-case sans.
          h('div', { class: 'tp-ev-name', style: { fontFamily: 'var(--tp-sans)' } }, c.name),
          h('div', { class: 'tp-ev-sub' }, c.description || '—')),
        h('span', { class: 'tp-ev-meta' }, `${n} event${n === 1 ? '' : 's'}`));
    });
    mountAll(listBody, nodes);
  }

  function selectCategory(id) {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    state.setDirty(false);
    draft = null; server = null; editingId = null;
    state.select('category', id);
  }

  async function newCategory() {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    try {
      const r = await persist('Category created', () =>
        api.doAction('create_category', { name: 'New category', color: SWATCHES[0] }, branch()));
      await state.reload();
      draft = null; server = null; editingId = null;
      state.select('category', r && r.id);
      setTimeout(() => { const n = detail.querySelector('#cat-name'); if (n) { n.focus(); n.select(); } }, 0);
    } catch (e) { /* persist surfaced the banner */ }
  }

  // ---- detail / buffered editor --------------------------------------------
  function renderDetail() {
    if (!plan()) { mountAll(detail, [h('div', { class: 'tp-empty' }, 'Loading…')]); return; }
    const sel = state.getState().selection;
    const c = sel.type === 'category' ? (plan().categories || []).find((x) => x.id === sel.id) : null;
    if (!c) {
      draft = null; server = null; editingId = null;
      mountAll(detail, [h('div', { class: 'tp-empty' }, h('div', {}, 'Select a category, or create one.'))]);
      return;
    }
    // (Re)snapshot when the selection changed underneath us (e.g. after reload).
    if (editingId !== c.id) snapshot(c);

    const counts = countByName(plan());
    const n = counts[c.name] || 0;

    const inner = h('div', {});
    inner.appendChild(headerSection(c, n));
    const body = h('div', { class: 'tp-ed-body' });
    body.appendChild(detailsCard());
    inner.appendChild(body);
    mountAll(detail, [inner]);
  }

  function headerSection(c, n) {
    const delBtn = h('button', { class: 'btn btn-ghost btn-sm', onClick: () => confirmDelete(c, n) }, 'Delete');

    const head = h('div', { class: 'tp-ed-head' },
      h('div', { class: 'tp-ed-id-row' },
        h('div', { class: 'tp-ed-id' },
          h('div', { class: 'tp-ed-kicker' }, 'Category'),
          // Header name reflects the DRAFT so it updates live as you type.
          h('div', { class: 'tp-ed-name', style: { fontFamily: 'var(--tp-sans)' } }, draft.name || 'Untitled')),
        h('div', { class: 'tp-ed-chips' },
          h('span', { class: 'tp-chip' }, `${n} event${n === 1 ? '' : 's'}`)),
        h('div', { class: 'tp-ed-actions' },
          delBtn,
          h('div', { class: 'tp-divv' }),
          saveCluster({ dirty: dirty(), saving, onSave: doSave, onDiscard: doDiscard }))));
    return head;
  }

  function detailsCard() {
    const card = h('div', { class: 'tp-card' });
    card.appendChild(h('div', { class: 'tp-card-h' }, h('h3', {}, 'Details')));
    const cb = h('div', { class: 'tp-card-b' });

    // Name (human label → sentence-case sans input, NOT mono).
    const nameInp = h('input', {
      class: 'input', id: 'cat-name', value: draft.name, placeholder: 'Category name',
      onInput: () => { draft.name = nameInp.value; setDirtyFlag(); renderHeaderOnly(); },
    });
    const nameField = h('div', { class: 'tp-field' },
      h('label', { class: 'tp-lbl' }, 'Name'), nameInp);

    // Description.
    const descInp = h('textarea', {
      class: 'textarea', placeholder: 'What groups under this category?',
      onInput: () => { draft.description = descInp.value; setDirtyFlag(); renderHeaderOnly(); },
    });
    descInp.value = draft.description || '';
    const descField = h('div', { class: 'tp-field tp-col-2', style: { marginTop: '16px' } },
      h('label', { class: 'tp-lbl' }, 'Description'), descInp);

    // Color swatches (the color is a value → swatch buttons).
    const swWrap = h('div', { class: 'tp-cat-swatches', style: { marginTop: '16px' } });
    const renderSwatches = () => {
      mountAll(swWrap, SWATCHES.map((col) =>
        h('button', {
          class: 'tp-swatch' + (draft.color === col ? ' is-active' : ''),
          style: { background: col }, title: col,
          onClick: () => { draft.color = col; setDirtyFlag(); renderSwatches(); renderHeaderOnly(); refreshDot(); },
        })));
    };
    renderSwatches();
    const dot = h('span', { class: 'tp-sd', id: 'cat-dot', style: { background: draft.color || 'var(--tp-text-4)' } });
    const colorField = h('div', { class: 'tp-field', style: { marginTop: '16px' } },
      h('label', { class: 'tp-lbl' }, 'Color'),
      h('div', { style: { display: 'flex', alignItems: 'center', gap: '12px' } }, dot, swWrap));

    cb.appendChild(nameField);
    cb.appendChild(descField);
    cb.appendChild(colorField);
    card.appendChild(cb);
    return card;
  }

  // Re-render only the sticky header so live edits don't steal input focus.
  function renderHeaderOnly() {
    const sel = state.getState().selection;
    const c = (plan().categories || []).find((x) => x.id === sel.id);
    if (!c) return;
    const counts = countByName(plan());
    const n = counts[c.name] || 0;
    const old = detail.querySelector('.tp-ed-head');
    if (old) old.replaceWith(headerSection(c, n));
  }

  function refreshDot() {
    const dot = detail.querySelector('#cat-dot');
    if (dot) dot.style.background = draft.color || 'var(--tp-text-4)';
  }

  function doDiscard() {
    draft = clone(server);
    state.setDirty(false);
    renderDetail();
  }

  async function doSave() {
    if (!draft || !editingId || saving) return;
    const name = (draft.name || '').trim();
    if (!name) { if (window.__tpBanner) window.__tpBanner('Name is required', 'err'); return; }
    saving = true;
    renderHeaderOnly();
    try {
      await persist('Saved', () => api.doAction('update_category', {
        category_id: editingId,
        name,
        color: draft.color || null,
        description: draft.description || null,
      }, branch()));
      await state.reload();
      // Re-snapshot from the fresh entity.
      const fresh = (plan().categories || []).find((x) => x.id === editingId);
      if (fresh) snapshot(fresh);
      saving = false;
      if (window.Fluxito && window.Fluxito.toast) window.Fluxito.toast('Saved', 'success');
      renderDetail();
    } catch (err) {
      saving = false;
      renderHeaderOnly();
      // persist surfaced the banner; draft kept.
    }
  }

  function confirmDelete(c, count) {
    const overlay = h('div', { class: 'modal-backdrop is-open' });
    const modal = h('div', { class: 'modal' });
    modal.appendChild(h('div', { class: 'modal-header' },
      h('div', { class: 'modal-title' }, `Delete category “${c.name}”?`)));
    if (count) {
      modal.appendChild(h('div', { class: 'modal-body' },
        h('div', { class: 'tp-warn', style: { padding: '12px 14px', borderRadius: 'var(--tp-r)' } },
          `${count} event${count === 1 ? '' : 's'} will be uncategorized (category cleared). The events are not deleted.`)));
    }
    const cancel = h('button', { class: 'btn btn-ghost btn-sm', onClick: () => overlay.remove() }, 'Cancel');
    const del = h('button', {
      class: 'btn btn-danger btn-sm',
      onClick: async () => {
        overlay.remove();
        try {
          await persist('Category deleted', () =>
            api.doAction('delete_category', { category_id: c.id }, branch()));
          draft = null; server = null; editingId = null;
          state.setDirty(false);
          state.select(null, null);
          await state.reload();
        } catch (e) { /* persist surfaced the banner */ }
      },
    }, 'Delete');
    modal.appendChild(h('div', { class: 'modal-footer' }, cancel, del));
    overlay.appendChild(modal);
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
  }

  return () => { unsub(); };
}

// ---- tiny inline icons (sans context, decorative) ---------------------------
function searchIcon() {
  return svg('M21 21l-4-4', [['circle', { cx: 11, cy: 11, r: 7 }]]);
}
function plusIcon() {
  const el = h('span', {});
  el.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 5v14M5 12h14"/></svg>';
  return el.firstChild;
}
function svg(path, extra) {
  const el = h('span', {});
  const extras = (extra || []).map(([t, a]) =>
    `<${t} ${Object.entries(a).map(([k, v]) => `${k}="${v}"`).join(' ')}/>`).join('');
  el.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${extras}<path d="${path}"/></svg>`;
  return el.firstChild;
}
