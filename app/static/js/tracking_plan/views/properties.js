// app/static/js/tracking_plan/views/properties.js
// Property library + editor, redesigned to the approved mockup
// (/tmp/tp_redesign_mockup.html) with the EXPLICIT BUFFERED-SAVE model.
//
// Master: refined list grouped by property kind, mono names, searchable.
// Detail: .tp-ed-head with the SAVE CLUSTER, then .tp-card sections —
//   • Type & flags   (name, kind, data_type, is_list / is_pii toggles)
//   • Constraints    (allowed values / min / max / regex + live regex-valid hint)
//   • Nested members (object / array children)
//   • Used by N events (event chips → navigate)
//
// BUFFERED SAVE (no autosave — nothing persists until "Save changes"):
//   On select, snapshot `server` and `draft = clone(editable fields)`. Render
//   from draft; every edit mutates draft ONLY and re-renders. dirty = !deepEqual.
//   Discard → draft = clone(server). Save → commitProperty() (one update_property
//   with assembled constraints) → reload → re-snapshot. The regex-valid gate
//   disables Save while the regex is invalid. Nested member add/remove stay
//   immediate sub-edits (structural, on the library) — the editable FIELDS buffer.

import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { mountDrawer } from 'tp/comments';
import { persist } from 'tp/util/persist';
import { clone, deepEqual, editorHead } from 'tp/util/editor';
import { allProps, typeBadge } from 'tp/util/format';
import { isValidRegex, buildConstraints, usedByEvents } from 'tp/util/constraints';

const KINDS = [
  ['event', 'Event'],
  ['user', 'User'],
  ['group', 'Group'],
  ['system', 'System'],
];
const DATA_TYPES = ['string', 'int', 'float', 'boolean', 'object', 'array'];

// data_type → badge color (mono badges, mockup palette). enum-like → sky.
function typeBadgeClass(dt) {
  if (dt === 'object' || dt === 'array') return 'tp-badge amber';
  if (dt === 'boolean') return 'tp-badge sky';
  return 'tp-badge ty';
}

// The editable slice we snapshot/diff for buffered save.
function draftOf(p) {
  return clone({
    name: p.name || '',
    kind: p.kind || 'event',
    data_type: p.data_type || 'string',
    is_list: !!p.is_list,
    is_pii: !!p.is_pii,
    description: p.description || '',
    constraints: p.constraints || null,
  });
}

export function mountView(container) {
  const layout = h('div', { class: 'tp-master-detail' });
  const master = h('div', { class: 'tp-master' });
  const detail = h('div', { class: 'tp-detail', id: 'tp-prop-detail' });
  layout.appendChild(master);
  layout.appendChild(detail);
  mountAll(container, [layout]);

  let search = '';
  let drawer = null;
  let drawerEntityId = null;

  // Buffered-save working state for the currently selected property.
  let server = null; // last server snapshot (editable slice)
  let draft = null; // live draft (editable slice) — editor renders from this
  let saving = false;

  // A pure setDirty() notification (same plan ref + same selection) must NOT
  // re-render the detail: that would steal focus from the input being typed in,
  // and because paint() ends with syncDirty() it would recurse. Mirror events.js —
  // track the last (plan, selection) rendered and ignore no-op notifications.
  // Real changes (reload, branch/entity switch) still re-render the detail;
  // buffered field edits refresh only the header in place (refreshHead).
  let lastPlan;
  let lastSelKey;
  const unsub = state.subscribe(() => {
    const st = state.getState();
    const selKey = st.selection.type + ':' + st.selection.id;
    if (st.plan === lastPlan && selKey === lastSelKey) return; // pure dirty toggle
    lastPlan = st.plan;
    lastSelKey = selKey;
    renderList();
    renderDetail();
  });
  renderList();
  renderDetail();

  function plan() { return state.getState().plan; }
  function dirty() { return !!server && !deepEqual(draft, server); }
  function syncDirty() { state.setDirty(dirty()); }

  // ---- MASTER -------------------------------------------------------------
  function renderList() {
    const head = h('div', { class: 'tp-master-head' },
      h('div', { class: 'tp-search' },
        h('input', {
          class: 'input', placeholder: 'Search properties', value: search,
          onInput: (e) => { search = e.target.value; renderListBody(listBody); },
        })),
      h('button', { class: 'btn btn-primary btn-sm btn-block', onClick: newProperty }, '+ New property'));
    const listBody = h('div', { class: 'tp-master-list' });
    renderListBody(listBody);
    mountAll(master, [head, listBody]);
  }

  function renderListBody(listBody) {
    if (!plan()) { mountAll(listBody, [h('div', { class: 'tp-row-empty' }, 'Loading…')]); return; }
    const sel = state.getState().selection;
    const q = search.trim().toLowerCase();
    const nodes = [];
    let any = false;
    KINDS.forEach(([k, label]) => {
      let items = (plan().properties && plan().properties[k]) || [];
      // Only top-level library props in the master; nested members live in their
      // parent's editor card.
      items = items.filter((p) => !p.parent_property_id);
      items = items.slice().sort((a, b) => a.name.localeCompare(b.name));
      if (q) items = items.filter((p) => p.name.toLowerCase().includes(q) || (p.description || '').toLowerCase().includes(q));
      if (!items.length) return;
      any = true;
      nodes.push(h('div', { class: 'tp-grp' }, `${label} properties`));
      items.forEach((p) => {
        nodes.push(h('div', {
          class: 'tp-ev' + (sel.type === 'property' && sel.id === p.id ? ' is-active' : ''),
          onClick: () => selectProperty(p.id),
        },
          h('span', { class: 'tp-sd' + (p.is_pii ? ' amber' : ' grey') }),
          h('div', { class: 'tp-ev-main' },
            h('div', { class: 'tp-ev-name' }, p.name),
            h('div', { class: 'tp-ev-sub' }, p.description || '—')),
          h('span', { class: 'tp-ev-meta' }, typeBadge(p.data_type, p.is_list))));
      });
    });
    if (!any) nodes.push(h('div', { class: 'tp-row-empty' }, q ? 'No matches' : 'No properties yet'));
    mountAll(listBody, nodes);
  }

  function selectProperty(id) {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    state.setDirty(false);
    server = null; draft = null; saving = false;
    state.select('property', id);
  }

  async function newProperty() {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    try {
      const r = await persist('Property created', () =>
        api.doAction('create_property', { name: 'new_property', data_type: 'string', kind: 'event' }, state.getState().branch));
      await state.reload();
      server = null; draft = null;
      state.setDirty(false);
      state.select('property', r.id);
      setTimeout(() => { const n = detail.querySelector('#pd-name'); if (n) { n.focus(); n.select(); } }, 0);
    } catch (err) { /* persist already surfaced the error banner */ }
  }

  // ---- DETAIL / EDITOR ----------------------------------------------------
  function currentProp() {
    const sel = state.getState().selection;
    if (sel.type !== 'property') return null;
    return allProps(plan() || {}).find((x) => x.id === sel.id) || null;
  }

  function renderDetail() {
    if (!plan()) { mountAll(detail, [h('div', { class: 'tp-empty' }, 'Loading…')]); return; }
    const p = currentProp();
    if (!p) {
      if (drawer) { drawer.destroy(); drawer = null; drawerEntityId = null; }
      server = null; draft = null;
      mountAll(detail, [h('div', { class: 'tp-empty' }, 'Select a property to edit its type & constraints, or create one.')]);
      return;
    }
    // (Re)snapshot when the selected property changes (or first render of it).
    if (!server || !draft || drawerEntityId !== p.id) {
      server = draftOf(p);
      draft = clone(server);
      saving = false;
    }
    paint(p);

    // Recreate the Comments drawer only when the selected property changes, so an
    // open panel survives field edits (which re-render the detail).
    if (drawerEntityId !== p.id) {
      if (drawer) { drawer.destroy(); drawer = null; }
      drawer = mountDrawer(document.querySelector('.tp-workspace') || document.body,
        { entityType: 'property', entityId: p.id, branch: state.getState().branch });
      drawerEntityId = p.id;
    }
  }

  // Re-render the editor body FROM draft (no resnapshot). Called after each edit.
  function repaint() {
    const p = currentProp();
    if (p) paint(p);
  }

  function paint(p) {
    const regexValid = isValidRegex((draft.constraints && draft.constraints.regex) || '');
    const inner = h('div', { class: 'tp-detail-inner' });
    inner.appendChild(header(p, regexValid));
    inner.appendChild(typeFlagsCard(p));
    inner.appendChild(constraintsCard(p));
    inner.appendChild(membersCard(p));
    inner.appendChild(usedByCard(p));
    mountAll(detail, [inner]);
    syncDirty();
  }

  // ---- editor header: kicker + mono name + chips + Comments/Delete + save ----
  function header(p, regexValid) {
    const kindLabel = (KINDS.find(([k]) => k === draft.kind) || [null, draft.kind])[1];
    const chips = [
      h('span', { class: 'tp-chip accent' }, kindLabel),
      h('span', { class: typeBadgeClass(draft.data_type) }, typeBadge(draft.data_type, draft.is_list)),
      draft.is_pii ? h('span', { class: 'tp-badge amber' }, 'PII') : null,
    ];
    const cmtBtn = h('button', { class: 'btn btn-ghost btn-sm', onClick: () => drawer && drawer.open() }, '💬 Comments');
    const delBtn = h('button', { class: 'btn btn-danger btn-sm', onClick: () => confirmDelete(p) }, 'Delete');

    const head = editorHead({
      kicker: 'Property',
      name: draft.name || p.name,
      chips,
      actions: [cmtBtn, delBtn],
      dirty: dirty(),
      saving,
      // Save gates on regex validity: drop the handler AND disable the button so
      // an invalid regex can never persist.
      onSave: regexValid ? () => doSave(p) : undefined,
      onDiscard: () => { draft = clone(server); repaint(); },
    });
    if (!regexValid) {
      const sb = head.querySelector('.tp-savecluster .btn-primary');
      if (sb) { sb.disabled = true; sb.title = 'Fix the invalid regex to save'; }
    }
    return head;
  }

  // ---- card: Type & flags ----
  function typeFlagsCard(p) {
    const nameInp = h('input', {
      class: 'input mono', id: 'pd-name', value: draft.name,
      onInput: (e) => { draft.name = e.target.value; syncDirty(); refreshHead(); },
    });

    const kindSel = h('select', {
      class: 'select', onChange: (e) => { draft.kind = e.target.value; repaint(); },
    }, ...KINDS.map(([k, label]) => h('option', { value: k, selected: draft.kind === k }, label)));

    const typeSel = h('select', {
      class: 'select', onChange: (e) => { draft.data_type = e.target.value; repaint(); },
    }, ...DATA_TYPES.map((t) => h('option', { value: t, selected: draft.data_type === t }, t)));

    const listToggle = toggleRow('List / array', 'Values are a list', draft.is_list,
      () => { draft.is_list = !draft.is_list; repaint(); });
    const piiToggle = toggleRow('PII', 'Contains personal data', draft.is_pii,
      () => { draft.is_pii = !draft.is_pii; repaint(); });

    const desc = h('textarea', { class: 'textarea', id: 'pd-desc', placeholder: 'What does this property capture?' });
    desc.value = draft.description || '';
    desc.addEventListener('input', () => { draft.description = desc.value; syncDirty(); refreshHead(); });

    return card('Type & flags', null,
      h('div', { class: 'tp-card-b' },
        h('div', { class: 'tp-field tp-col-2', style: { marginBottom: '16px' } },
          h('label', { class: 'tp-lbl' }, 'Name'), nameInp),
        h('div', { class: 'tp-grid2' },
          field('Kind', kindSel),
          field('Data type', typeSel),
          listToggle,
          piiToggle),
        h('div', { class: 'tp-field tp-col-2', style: { marginTop: '16px' } },
          h('label', { class: 'tp-lbl' }, 'Description'), desc)));
  }

  // Rebuild the sticky header in place after a buffered field edit — refreshes the
  // mono name mirror, chips, and the save cluster (● Unsaved / Save enabled/gated)
  // WITHOUT re-rendering the cards, so the focused input keeps its caret. Every
  // editable input lives in a card (#pd-name, #pd-desc, #pd-enum/min/max/regex),
  // never in the header, so replacing the header never steals focus.
  function refreshHead() {
    const head = detail.querySelector('.tp-ed-head');
    if (!head) return;
    const regexValid = isValidRegex((draft.constraints && draft.constraints.regex) || '');
    head.replaceWith(header(currentProp(), regexValid));
  }

  // ---- card: Constraints (live regex hint, applies vs draft.constraints) ----
  function constraintsCard(p) {
    const c = draft.constraints || {};
    const numeric = draft.data_type === 'int' || draft.data_type === 'float';

    const enumInp = h('input', {
      class: 'input mono', id: 'pd-enum', placeholder: 'USD, EUR, GBP',
      value: (c.allowed_values || []).join(', '),
    });
    const minInp = h('input', { class: 'input', id: 'pd-min', type: 'number', value: c.min ?? '', placeholder: 'min' });
    const maxInp = h('input', { class: 'input', id: 'pd-max', type: 'number', value: c.max ?? '', placeholder: 'max' });
    const regexInp = h('input', { class: 'input mono', id: 'pd-regex', placeholder: '^[A-Z]{3}$', value: c.regex || '' });
    const regexHint = h('span', { class: 'tp-regex-hint' });

    const recompute = () => {
      const next = buildConstraints({
        enumRaw: enumInp.value,
        min: minInp.value,
        max: maxInp.value,
        regex: regexInp.value,
      });
      draft.constraints = next;
      syncDirty();
      refreshHead();
    };
    const refreshRegexHint = () => {
      const val = regexInp.value;
      const ok = isValidRegex(val);
      regexHint.textContent = val.trim() ? (ok ? '✓ valid regex' : '✗ invalid regex') : '';
      regexHint.className = 'tp-regex-hint ' + (ok ? 'is-ok' : 'is-bad');
      // Toggle the Save button enabled state without a full repaint.
      const sb = detail.querySelector('.tp-ed-head .tp-savecluster .btn-primary');
      if (sb) {
        if (val.trim() && !ok) { sb.disabled = true; sb.title = 'Fix the invalid regex to save'; }
        else if (dirty()) { sb.disabled = !!saving; sb.removeAttribute('title'); }
      }
    };

    [enumInp, minInp, maxInp].forEach((el) => el.addEventListener('input', recompute));
    regexInp.addEventListener('input', () => { recompute(); refreshRegexHint(); });
    refreshRegexHint();

    const regexField = h('div', { class: 'tp-field tp-col-2' },
      h('label', { class: 'tp-lbl' }, 'Regex / format'), regexInp, regexHint);

    const body = h('div', { class: 'tp-card-b' },
      h('div', { class: 'tp-grid2' },
        h('div', { class: 'tp-field tp-col-2' },
          h('label', { class: 'tp-lbl' }, 'Allowed values (enum) — comma separated'), enumInp),
        field(numeric ? 'Min' : 'Min length', minInp),
        field(numeric ? 'Max' : 'Max length', maxInp),
        regexField));
    return card('Constraints', null, body);
  }

  // ---- card: Nested members (immediate structural sub-edits) ----
  function membersCard(p) {
    const isContainer = draft.data_type === 'object' || draft.data_type === 'array';
    const children = allProps(plan()).filter((x) => x.parent_property_id === p.id);

    if (!isContainer) {
      return card('Nested members', null,
        h('div', { class: 'tp-card-b' },
          h('div', { class: 'tp-muted', style: { fontSize: '13px' } },
            'Set the data type to object or array to add members.')));
    }

    const tbody = h('tbody');
    if (!children.length) {
      tbody.appendChild(h('tr', {}, h('td', { class: 'tp-muted', colspan: '3', style: { padding: '14px' } }, 'No members yet.')));
    } else {
      children.forEach((child) => {
        tbody.appendChild(h('tr', {},
          h('td', { class: 'tp-pn' }, child.name),
          h('td', {}, h('span', { class: typeBadgeClass(child.data_type) }, typeBadge(child.data_type, child.is_list))),
          h('td', { class: 'tp-cell-act' },
            h('button', { class: 'btn btn-ghost btn-sm', onClick: () => removeMember(child) }, 'Remove'))));
      });
    }

    const nm = h('input', { class: 'input mono', placeholder: 'member name', style: { width: '160px' } });
    const ty = h('select', { class: 'select' }, ...DATA_TYPES.map((t) => h('option', { value: t }, t)));
    const addBtn = h('button', { class: 'btn btn-secondary btn-sm', onClick: () => addMember(p, nm, ty) }, 'Add member');

    const note = dirty()
      ? h('div', { class: 'tp-muted', style: { fontSize: '11.5px', marginTop: '8px' } },
        'Member changes save immediately; field edits above stay buffered until you click Save.')
      : null;

    const body = h('div', { class: 'tp-card-b' },
      h('table', { class: 'tp-itable' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Name'), h('th', { style: { width: '120px' } }, 'Type'), h('th', { style: { width: '90px' } }))),
        tbody),
      h('div', { class: 'tp-inline-add', style: { marginTop: '12px' } }, nm, ty, addBtn),
      note);
    return card('Nested members', children.length ? String(children.length) : null, body);
  }

  async function addMember(parent, nm, ty) {
    const name = nm.value.trim();
    if (!name) return;
    try {
      await persist('Member added', () =>
        api.doAction('create_property', {
          name, data_type: ty.value, kind: draft.kind || parent.kind, parent_property_id: parent.id,
        }, state.getState().branch));
      await state.reload();
      repaint();
    } catch (err) { /* persist already surfaced the error banner */ }
  }

  async function removeMember(child) {
    try {
      await persist('Member removed', () =>
        api.doAction('delete_property', { property_id: child.id }, state.getState().branch));
      await state.reload();
      repaint();
    } catch (err) { /* persist already surfaced the error banner */ }
  }

  // ---- card: Used by N events ----
  function usedByCard(p) {
    const events = usedByEvents(plan(), p);
    if (!events.length) {
      return card('Used by 0 events', null,
        h('div', { class: 'tp-card-b' },
          h('div', { class: 'tp-muted', style: { fontSize: '13px' } }, 'Not attached to any event.')));
    }
    const wrap = h('div', { class: 'tp-usedby' });
    events.forEach((e) => {
      wrap.appendChild(h('button', {
        class: 'tp-usedby-chip',
        onClick: () => {
          if (dirty() && !confirm('Discard unsaved changes?')) return;
          state.setDirty(false);
          state.setView('events');
          state.select('event', e.id);
        },
      }, e.name));
    });
    return card(`Used by ${events.length} event${events.length === 1 ? '' : 's'}`, null,
      h('div', { class: 'tp-card-b' }, wrap));
  }

  // ---- commit (buffered save): make server match draft ----
  async function doSave(p) {
    if (saving) return;
    if (!isValidRegex((draft.constraints && draft.constraints.regex) || '')) return;
    saving = true;
    repaint();
    try {
      await persist('Saved', () => commitProperty(p));
      await state.reload();
      const fresh = currentProp();
      server = draftOf(fresh || p);
      draft = clone(server);
      saving = false;
      state.setDirty(false);
      repaint();
    } catch (err) {
      saving = false;
      repaint(); // keep the draft; banner already shown by persist
    }
  }

  // One idempotent update_property carrying the assembled constraints. We rebuild
  // constraints from draft via buildConstraints so the persisted shape matches the
  // service contract (allowed_values/min/max/regex, or null when empty).
  function commitProperty(p) {
    const c = draft.constraints || {};
    const constraints = buildConstraints({
      enumRaw: (c.allowed_values || []).join(', '),
      min: c.min ?? '',
      max: c.max ?? '',
      regex: c.regex || '',
    });
    return api.doAction('update_property', {
      property_id: p.id,
      name: (draft.name || '').trim(),
      kind: draft.kind,
      data_type: draft.data_type,
      is_list: !!draft.is_list,
      is_pii: !!draft.is_pii,
      description: draft.description ? draft.description : null,
      constraints,
    }, state.getState().branch);
  }

  // ---- delete (canonical .modal-backdrop confirm, lists dependent events) ----
  function confirmDelete(p) {
    const events = usedByEvents(plan(), p);
    const overlay = h('div', { class: 'modal-backdrop is-open' });
    const body = events.length
      ? h('div', { class: 'tp-warn' },
        `In use by ${events.length} event${events.length === 1 ? '' : 's'}: ${events.map((e) => e.name).join(', ')}. `
        + 'Deleting also removes those attachments.')
      : h('div', { class: 'tp-muted', style: { fontSize: '13px' } }, 'This property is not attached to any event.');

    const cancel = h('button', { class: 'btn btn-ghost', onClick: () => overlay.remove() }, 'Cancel');
    const del = h('button', {
      class: 'btn btn-danger',
      onClick: async () => {
        overlay.remove();
        try {
          await persist('Property deleted', () => api.doAction('delete_property', { property_id: p.id }, state.getState().branch));
          server = null; draft = null;
          state.setDirty(false);
          state.select('property', null);
          await state.reload();
        } catch (err) { /* persist already surfaced the error banner */ }
      },
    }, 'Delete');

    const modal = h('div', { class: 'modal' },
      h('div', { class: 'modal-header' }, h('div', { class: 'modal-title' }, `Delete property "${p.name}"?`)),
      h('div', { class: 'modal-body' }, body),
      h('div', { class: 'modal-footer' }, cancel, del));
    overlay.appendChild(modal);
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
  }

  return () => { unsub(); if (drawer) drawer.destroy(); };
}

// ---- small DOM helpers --------------------------------------------------
function card(title, count, ...bodyNodes) {
  const headChildren = [h('h3', {}, title)];
  if (count) headChildren.push(h('span', { class: 'tp-ct' }, count));
  return h('div', { class: 'tp-card' },
    h('div', { class: 'tp-card-h' }, ...headChildren),
    ...bodyNodes);
}

function field(label, control) {
  return h('div', { class: 'tp-field' }, h('label', { class: 'tp-lbl' }, label), control);
}

// A labeled mockup-style toggle switch (.tp-toggle) inside a .tp-field.
function toggleRow(label, sub, on, onToggle) {
  const sw = h('div', { class: 'tp-toggle' + (on ? ' on' : ''), role: 'switch', 'aria-checked': on ? 'true' : 'false' });
  sw.addEventListener('click', onToggle);
  return h('div', { class: 'tp-field' },
    h('label', { class: 'tp-lbl' }, label),
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px' } },
      sw,
      h('span', { class: 'tp-muted', style: { fontSize: '12px' } }, sub)));
}
