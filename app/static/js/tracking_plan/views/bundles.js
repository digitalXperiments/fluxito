// app/static/js/tracking_plan/views/bundles.js
// Property bundles — refined master-detail (best-in-class redesign).
// Master: searchable list of bundles with property count. Detail: a buffered
// editor. Selecting a bundle snapshots draft={name, description, properties[]};
// EVERY edit (name/desc, add/remove property, toggle required, drag-reorder)
// mutates the DRAFT only and re-renders. The save cluster commits everything in
// one shot on Save (snapshot-sync):
//   • update_bundle(id, {name, description})
//   • for each draft property → add_property_to_bundle (upsert: required +
//     sort_order = its index in the draft order)
//   • for each server property absent from the draft → remove_property_from_bundle
// Attach-to-event is a separate immediate action (it copies the bundle's
// properties into a *different* entity — not part of the bundle's own draft).
//
// BUFFERED-SAVE MODEL (tp/util/editor): nothing hits the API on edit. dirty is
// !deepEqual(draft, server); the shell guards navigation on state.dirty.

import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { persist } from 'tp/util/persist';
import { clone, isDirty, saveCluster } from 'tp/util/editor';
import { allProps, eventProps, typeBadge } from 'tp/util/format';

export function mountView(container) {
  const layout = h('div', { class: 'tp-master-detail' });
  const master = h('div', { class: 'tp-master' });
  const detail = h('div', { class: 'tp-detail', id: 'tp-bundle-detail' });
  layout.appendChild(master);
  layout.appendChild(detail);
  mountAll(container, [layout]);

  let search = '';
  // Editor buffer for the selected bundle.
  let draft = null;     // { name, description, properties: [{property_id, name, data_type, required, sort_order}] }
  let server = null;    // snapshot of the same shape from the loaded entity
  let saving = false;
  let editingId = null; // which bundle the draft belongs to

  // The subscriber always refreshes the (cheap) list. It re-renders the detail
  // only when the focus is NOT inside the detail pane — otherwise a self-notify
  // from setDirty() during typing would blow away the focused input. Structural
  // edits (add/remove/reorder/toggle) call renderDetail() directly themselves,
  // so they don't rely on this path.
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

  // Snapshot the editable fields of a bundle into draft + server. Properties are
  // normalized to draft order (already sort_order-sorted by the serializer).
  function snapshot(b) {
    // is_list isn't on the bundle property dict — resolve it from the library by
    // id so the type badge is consistent with newly-added (unsaved) rows.
    const libById = {};
    allProps(plan()).forEach((p) => { libById[p.id] = p; });
    const props = (b.properties || []).slice()
      .sort((x, y) => x.sort_order - y.sort_order)
      .map((p, i) => ({
        property_id: p.property_id,
        name: p.name,
        data_type: p.data_type,
        is_list: libById[p.property_id] ? !!libById[p.property_id].is_list : false,
        required: !!p.required,
        sort_order: i,
      }));
    server = { name: b.name || '', description: b.description || '', properties: props };
    draft = clone(server);
    editingId = b.id;
    state.setDirty(false);
  }

  // ---- master list ----------------------------------------------------------
  function renderList() {
    const head = h('div', { class: 'tp-master-head' },
      h('div', { class: 'tp-search' },
        searchIcon(),
        h('input', {
          class: 'input', placeholder: 'Search bundles', value: search,
          onInput: (e) => { search = e.target.value; renderListBody(listBody); },
        })),
      h('button', { class: 'btn btn-primary btn-sm btn-block', onClick: newBundle },
        plusIcon(), 'New bundle'));
    const listBody = h('div', { class: 'tp-master-list' });
    renderListBody(listBody);
    mountAll(master, [head, listBody]);
  }

  function renderListBody(listBody) {
    if (!plan()) { mountAll(listBody, [h('div', { class: 'tp-row-empty' }, 'Loading…')]); return; }
    const sel = state.getState().selection;
    let bundles = (plan().bundles || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    if (search) {
      const q = search.toLowerCase();
      bundles = bundles.filter((b) => b.name.toLowerCase().includes(q));
    }
    if (!bundles.length) {
      mountAll(listBody, [h('div', { class: 'tp-row-empty' },
        search ? 'No matching bundles.' : 'No bundles yet. Group reusable properties into a bundle.')]);
      return;
    }
    const nodes = bundles.map((b) => {
      const n = b.properties.length;
      return h('div', {
        class: 'tp-ev' + (sel.type === 'bundle' && sel.id === b.id ? ' is-active' : ''),
        onClick: () => selectBundle(b.id),
      },
        h('span', { class: 'tp-sd grey' }),
        h('div', { class: 'tp-ev-main' },
          // Bundle names are human labels, not identifiers → sentence-case sans.
          h('div', { class: 'tp-ev-name', style: { fontFamily: 'var(--tp-sans)' } }, b.name),
          h('div', { class: 'tp-ev-sub' }, b.description || '—')),
        h('span', { class: 'tp-ev-meta' }, `${n} prop${n === 1 ? '' : 's'}`));
    });
    mountAll(listBody, nodes);
  }

  function selectBundle(id) {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    state.setDirty(false);
    draft = null; server = null; editingId = null;
    state.select('bundle', id);
  }

  async function newBundle() {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    try {
      const r = await persist('Bundle created', () =>
        api.doAction('create_bundle', { name: 'New bundle' }, branch()));
      await state.reload();
      draft = null; server = null; editingId = null;
      state.select('bundle', r && r.id);
      setTimeout(() => { const n = detail.querySelector('#bundle-name'); if (n) { n.focus(); n.select(); } }, 0);
    } catch (e) { /* persist surfaced the banner */ }
  }

  // ---- detail / buffered editor --------------------------------------------
  function renderDetail() {
    if (!plan()) { mountAll(detail, [h('div', { class: 'tp-empty' }, 'Loading…')]); return; }
    const sel = state.getState().selection;
    const b = sel.type === 'bundle' ? (plan().bundles || []).find((x) => x.id === sel.id) : null;
    if (!b) {
      draft = null; server = null; editingId = null;
      mountAll(detail, [h('div', { class: 'tp-empty' }, h('div', {}, 'Select a bundle to build it, or create one.'))]);
      return;
    }
    if (editingId !== b.id) snapshot(b);

    const inner = h('div', {});
    inner.appendChild(headerSection(b));
    const body = h('div', { class: 'tp-ed-body' });
    body.appendChild(detailsCard());
    body.appendChild(propertiesCard());
    body.appendChild(attachCard(b));
    inner.appendChild(body);
    mountAll(detail, [inner]);
  }

  function headerSection(b) {
    const n = draft.properties.length;
    const delBtn = h('button', { class: 'btn btn-ghost btn-sm', onClick: () => delBundle(b) }, 'Delete');
    return h('div', { class: 'tp-ed-head' },
      h('div', { class: 'tp-ed-id-row' },
        h('div', { class: 'tp-ed-id' },
          h('div', { class: 'tp-ed-kicker' }, 'Bundle'),
          h('div', { class: 'tp-ed-name', style: { fontFamily: 'var(--tp-sans)' } }, draft.name || 'Untitled')),
        h('div', { class: 'tp-ed-chips' },
          h('span', { class: 'tp-chip' }, `${n} prop${n === 1 ? '' : 's'}`)),
        h('div', { class: 'tp-ed-actions' },
          delBtn,
          h('div', { class: 'tp-divv' }),
          saveCluster({ dirty: dirty(), saving, onSave: doSave, onDiscard: doDiscard }))));
  }

  // Re-render only the sticky header (live name/dirty without stealing focus).
  function renderHeaderOnly() {
    const sel = state.getState().selection;
    const b = (plan().bundles || []).find((x) => x.id === sel.id);
    if (!b) return;
    const old = detail.querySelector('.tp-ed-head');
    if (old) old.replaceWith(headerSection(b));
  }

  function detailsCard() {
    const card = h('div', { class: 'tp-card' });
    card.appendChild(h('div', { class: 'tp-card-h' }, h('h3', {}, 'Details')));
    const cb = h('div', { class: 'tp-card-b' });

    const nameInp = h('input', {
      class: 'input', id: 'bundle-name', value: draft.name, placeholder: 'Bundle name',
      onInput: () => { draft.name = nameInp.value; setDirtyFlag(); renderHeaderOnly(); },
    });
    const descInp = h('textarea', {
      class: 'textarea', placeholder: 'What is this bundle for?',
      onInput: () => { draft.description = descInp.value; setDirtyFlag(); renderHeaderOnly(); },
    });
    descInp.value = draft.description || '';

    cb.appendChild(h('div', { class: 'tp-field' }, h('label', { class: 'tp-lbl' }, 'Name'), nameInp));
    cb.appendChild(h('div', { class: 'tp-field tp-col-2', style: { marginTop: '16px' } },
      h('label', { class: 'tp-lbl' }, 'Description'), descInp));
    card.appendChild(cb);
    return card;
  }

  // ---- properties card: data-table + drag-reorder + required toggle + combo --
  function propertiesCard() {
    const card = h('div', { class: 'tp-card' });
    card.appendChild(h('div', { class: 'tp-card-h' },
      h('h3', {}, 'Properties'),
      h('span', { class: 'tp-ct' }, String(draft.properties.length))));
    const cb = h('div', { class: 'tp-card-b' });

    const tbody = h('tbody');
    draft.properties.forEach((p, idx) => tbody.appendChild(propertyRow(p, idx)));
    if (!draft.properties.length) {
      tbody.appendChild(h('tr', {}, h('td', {
        class: 'tp-muted', colspan: '5', style: { padding: '14px' },
      }, 'No properties yet — add one below.')));
    }
    cb.appendChild(h('table', { class: 'tp-ptable' },
      h('thead', {}, h('tr', {},
        h('th', { style: { width: '18px' } }),
        h('th', {}, 'Name'),
        h('th', { style: { width: '110px' } }, 'Type'),
        h('th', { style: { width: '90px' } }, 'Required'),
        h('th', { style: { width: '90px' } }))),
      tbody));
    cb.appendChild(addCombo());
    card.appendChild(cb);
    return card;
  }

  function propertyRow(p, idx) {
    const grip = h('td', { class: 'tp-grip' }, '⠿');
    const toggle = h('button', {
      class: 'tp-toggle' + (p.required ? ' on' : ''),
      title: p.required ? 'Required' : 'Optional',
      onClick: () => { p.required = !p.required; setDirtyFlag(); toggle.classList.toggle('on', p.required); renderHeaderOnly(); },
    });
    const rm = h('button', {
      class: 'btn btn-ghost btn-sm',
      onClick: () => { draft.properties.splice(idx, 1); reindex(); setDirtyFlag(); renderDetail(); },
    }, 'Remove');

    const row = h('tr', { class: 'tp-prow', draggable: 'true', dataset: { idx: String(idx) } },
      grip,
      h('td', {}, h('span', { class: 'tp-pn' }, p.name)),
      h('td', {}, h('span', { class: 'tp-badge ty' }, typeBadge(p.data_type, p.is_list))),
      h('td', {}, toggle),
      h('td', { class: 'tp-cell-act' }, rm));
    wireDrag(row, idx);
    return row;
  }

  // Drag-to-reorder within the DRAFT (no API call). On drop we splice the draft
  // order, reindex sort_order, mark dirty and re-render. Commit persists order.
  function wireDrag(row, idx) {
    row.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.setData('text/plain', String(idx));
      row.classList.add('is-dragging');
    });
    row.addEventListener('dragend', () => row.classList.remove('is-dragging'));
    row.addEventListener('dragover', (ev) => ev.preventDefault());
    row.addEventListener('drop', (ev) => {
      ev.preventDefault();
      const from = Number(ev.dataTransfer.getData('text/plain'));
      const to = idx;
      if (Number.isNaN(from) || from === to) return;
      const [moved] = draft.properties.splice(from, 1);
      draft.properties.splice(to, 0, moved);
      reindex();
      setDirtyFlag();
      renderDetail();
    });
  }

  function reindex() {
    draft.properties.forEach((p, i) => { p.sort_order = i; });
  }

  // Add-a-property combobox over the event-property library (mockup .tp-combo).
  function addCombo() {
    const inBundle = new Set(draft.properties.map((p) => p.property_id));
    const lib = eventProps(plan()).filter((p) => !inBundle.has(p.id));
    const input = h('input', {
      class: 'tp-combo-input',
      placeholder: 'Add a property — search the library…',
    });
    const pop = h('div', { class: 'tp-combo-pop', style: { display: 'none' } });
    const wrap = h('div', { class: 'tp-combo' }, comboPlusIcon(), input, pop);

    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      const hits = lib.filter((p) => p.name.toLowerCase().includes(q)).slice(0, 8);
      const opts = hits.map((p) => h('div', {
        class: 'tp-combo-opt',
        onClick: () => addProperty(p),
      }, h('span', { class: 'tp-pn' }, p.name),
         h('span', { class: 'tp-badge ty' }, typeBadge(p.data_type, p.is_list))));
      mountAll(pop, opts.length
        ? opts
        : [h('div', { class: 'tp-muted', style: { padding: '8px 10px' } }, 'No matching library properties.')]);
      pop.style.display = 'block';
    });
    input.addEventListener('blur', () => setTimeout(() => { pop.style.display = 'none'; }, 150));
    return wrap;
  }

  function addProperty(libProp) {
    if (draft.properties.some((p) => p.property_id === libProp.id)) return;
    draft.properties.push({
      property_id: libProp.id,
      name: libProp.name,
      data_type: libProp.data_type,
      is_list: libProp.is_list,
      required: false,
      sort_order: draft.properties.length,
    });
    reindex();
    setDirtyFlag();
    renderDetail();
  }

  // ---- attach-to-event (immediate; copies into a different entity) ----------
  function attachCard(b) {
    const card = h('div', { class: 'tp-card' });
    card.appendChild(h('div', { class: 'tp-card-h' }, h('h3', {}, 'Attach to event')));
    const cb = h('div', { class: 'tp-card-b' });
    cb.appendChild(h('div', { class: 'tp-lbl', style: { marginBottom: '10px' } },
      'Copy this bundle’s properties onto an event. Applies immediately — it is not part of the bundle draft.'));

    const evSel = h('select', { class: 'select' },
      h('option', { value: '' }, 'Choose an event…'),
      ...(plan().events || []).slice().sort((a, c) => a.name.localeCompare(c.name))
        .map((e) => h('option', { value: e.id }, e.name)));
    const attachBtn = h('button', {
      class: 'btn btn-secondary btn-sm',
      onClick: async () => {
        if (!evSel.value) return;
        if (dirty() && !confirm('You have unsaved bundle changes. Attach using the saved (server) bundle?')) return;
        try {
          await persist('Bundle attached', () =>
            api.doAction('attach_bundle_to_event', { event_id: evSel.value, bundle_id: b.id }, branch()));
          await state.reload();
        } catch (e) { /* persist surfaced the banner */ }
      },
    }, 'Attach bundle');
    cb.appendChild(h('div', { class: 'tp-inline-add' }, evSel, attachBtn));
    card.appendChild(cb);
    return card;
  }

  // ---- save / discard / delete ---------------------------------------------
  function doDiscard() {
    draft = clone(server);
    state.setDirty(false);
    renderDetail();
  }

  // Commit makes the SERVER match the DRAFT (snapshot-sync):
  //   1) update_bundle scalars
  //   2) upsert every draft property (required + sort_order = index)
  //   3) remove server properties absent from the draft
  async function doSave() {
    if (!draft || !editingId || saving) return;
    const name = (draft.name || '').trim();
    if (!name) { if (window.__tpBanner) window.__tpBanner('Name is required', 'err'); return; }
    saving = true;
    renderHeaderOnly();
    const id = editingId;
    const draftIds = new Set(draft.properties.map((p) => p.property_id));
    const toRemove = server.properties.filter((p) => !draftIds.has(p.property_id));
    try {
      await persist('Saved', async () => {
        await api.doAction('update_bundle', {
          bundle_id: id, name, description: draft.description || null,
        }, branch());
        for (let i = 0; i < draft.properties.length; i++) {
          const p = draft.properties[i];
          await api.doAction('add_property_to_bundle', {
            bundle_id: id, property_id: p.property_id, required: !!p.required, sort_order: i,
          }, branch());
        }
        for (const p of toRemove) {
          await api.doAction('remove_property_from_bundle', {
            bundle_id: id, property_id: p.property_id,
          }, branch());
        }
      });
      await state.reload();
      const fresh = (plan().bundles || []).find((x) => x.id === id);
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

  function delBundle(b) {
    const overlay = h('div', { class: 'modal-backdrop is-open' });
    const modal = h('div', { class: 'modal' });
    modal.appendChild(h('div', { class: 'modal-header' },
      h('div', { class: 'modal-title' }, `Delete bundle “${b.name}”?`)));
    modal.appendChild(h('div', { class: 'modal-body' },
      h('div', {}, 'The bundle is removed. Events that already had its properties copied keep them.')));
    const cancel = h('button', { class: 'btn btn-ghost btn-sm', onClick: () => overlay.remove() }, 'Cancel');
    const del = h('button', {
      class: 'btn btn-danger btn-sm',
      onClick: async () => {
        overlay.remove();
        try {
          await persist('Bundle deleted', () =>
            api.doAction('delete_bundle', { bundle_id: b.id }, branch()));
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
  const el = h('span', {});
  el.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>';
  return el.firstChild;
}
function plusIcon() {
  const el = h('span', {});
  el.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 5v14M5 12h14"/></svg>';
  return el.firstChild;
}
function comboPlusIcon() {
  const el = h('span', {});
  el.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>';
  return el.firstChild;
}
