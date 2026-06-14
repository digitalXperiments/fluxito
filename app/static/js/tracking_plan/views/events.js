// app/static/js/tracking_plan/views/events.js
// Redesigned Event editor (Avo-grade, mockup /tmp/tp_redesign_mockup.html) with the
// EXPLICIT BUFFERED-SAVE model — nothing hits the API on edit; everything commits on Save.
//
// LAYOUT
//   master  : .tp-search + .btn.btn-primary '+ New event' + grouped .tp-ev rows
//   detail  : sticky .tp-ed-head (kicker 'Event' + mono name + chips + actions + SAVE CLUSTER)
//             body of .tp-card sections — Details / Properties / Tracked on / Destinations
//
// SAVE FLOW (per tp/util/editor)
//   selectEvent(id) → snapshot server = clone(editable fields+collections of E),
//                     draft = clone(server); render detail FROM draft.
//   every field/collection edit mutates `draft` ONLY → recompute dirty → re-render detail.
//   header save cluster: dirty → '● Unsaved' + Discard + Save(enabled); clean → 'Saved'.
//   Discard → draft = clone(server); dirty=false; re-render.
//   Save → saving=true; commitEvent(draft, server, branch) [snapshot-sync via idempotent/
//          replace-all actions]; await state.reload(); re-snapshot server+draft from the
//          fresh event; dirty=false; toast 'Saved'. On error keep draft + banner.
//   Navigation guard: selecting a different event while dirty → confirm('Discard…').
//
// commitEvent snapshot-sync:
//   • scalars      → one update_event(id, {changed fields})  (category name→id via plan)
//   • properties   → attach_property UPSERT per draft prop (resolve name→library id, or
//                    create_property first for typed-new), detach_property for server props
//                    absent from draft.
//   • sources      → set_event_sources(event_id, draft.sources) [replace-all]
//   • destinations → set_event_destination(each draft dest), remove_event_destination(any
//                    server dest absent from draft)

import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { mountDrawer } from 'tp/comments';
import { clone, isDirty } from 'tp/util/editor';
import {
  eventStatus, typeBadge, propByName, sourceByName, destByName, catByName,
} from 'tp/util/format';

const IMPL_STATUSES = ['planned', 'implemented', 'verified', 'deprecated'];

// Pick the editable slice of an event we buffer in the draft (scalars + collections).
function snapshotOf(e) {
  return {
    name: e.name || '',
    display_name: e.display_name || '',
    description: e.description || '',
    category: e.category || '',
    trigger_type: e.trigger_type || '',
    purpose: e.purpose || '',
    owner_business: e.owner_business || '',
    owner_technical: e.owner_technical || '',
    properties: (e.properties || []).map((p) => ({
      name: p.name,
      data_type: p.data_type,
      is_list: !!p.is_list,
      required: !!p.required,
      example: p.example || '',
      override_description: p.override_description || '',
    })),
    sources: (e.sources || []).map((s) => ({
      name: s.name,
      implementation_status: s.implementation_status || 'planned',
    })),
    destinations: (e.destinations || []).map((d) => ({
      destination: d.destination,
      dest_event_name: d.dest_event_name || '',
      enabled: d.enabled !== false,
    })),
  };
}

// Badge color class from data type (mockup: numbers→sky, boolean→amber, object/array→green).
function badgeClass(dataType) {
  switch (dataType) {
    case 'int':
    case 'float':
      return 'sky';
    case 'boolean':
      return 'amber';
    case 'object':
    case 'array':
      return 'green';
    default:
      return 'ty';
  }
}

export function mountView(container) {
  const layout = h('div', { class: 'tp-master-detail' });
  const master = h('div', { class: 'tp-master' });
  const detail = h('div', { class: 'tp-detail', id: 'tp-ev-detail' });
  layout.appendChild(master);
  layout.appendChild(detail);
  mountAll(container, [layout]);

  let search = '';
  let drawer = null;
  let drawerEntityId = null;

  // ---- buffered-save editor state (per selected event) ----
  let editId = null; // event id the draft belongs to
  let server = null; // clone of the server snapshot
  let draft = null; // clone the editor renders from
  let saving = false;

  function dirty() {
    return !!draft && isDirty(draft, server);
  }
  // Per-keystroke edits only need the nav-guard dirty flag updated; they must NOT
  // trigger a full re-render (that would steal input focus). state.setDirty()
  // notifies subscribers, so we guard our subscriber below to ignore pure dirty
  // toggles (identical plan ref + selection) and only re-render on real changes.
  function syncDirty() {
    state.setDirty(dirty());
  }

  // Track what we last rendered so a pure setDirty() notification is a no-op.
  let lastPlan = undefined;
  let lastSelKey = undefined;
  const unsub = state.subscribe(() => {
    const st = state.getState();
    const selKey = st.selection.type + ':' + st.selection.id;
    if (st.plan === lastPlan && selKey === lastSelKey) return; // pure dirty toggle
    lastPlan = st.plan;
    lastSelKey = selKey;
    renderList();
    onStateChange();
  });
  renderList();
  loadSelection();

  function plan() { return state.getState().plan; }
  function curEvent() {
    const sel = state.getState().selection;
    return sel.type === 'event' ? (plan().events || []).find((x) => x.id === sel.id) : null;
  }

  // A state change (reload, branch switch, external select) may have changed the
  // selected event. Reconcile the draft: adopt the new selection if it differs.
  function onStateChange() {
    const e = curEvent();
    if (!e) {
      if (editId !== null) { editId = null; server = null; draft = null; }
      renderDetail();
      return;
    }
    if (e.id !== editId) {
      // selection changed externally (or first load) → fresh snapshot
      editId = e.id;
      server = clone(snapshotOf(e));
      draft = clone(server);
      saving = false;
      syncDirty();
    }
    renderDetail();
  }

  function loadSelection() {
    onStateChange();
  }

  // ============================ MASTER LIST ============================
  function renderList() {
    const head = h('div', { class: 'tp-master-head' },
      h('div', { class: 'tp-search' },
        searchIcon(),
        h('input', {
          class: 'input', placeholder: 'Search events', value: search,
          onInput: (e) => { search = e.target.value; renderListBody(listBody); },
        })),
      h('button', { class: 'btn btn-primary btn-sm btn-block', onClick: newEvent },
        plusIcon(), ' New event'));
    const listBody = h('div', { class: 'tp-master-list' });
    renderListBody(listBody);
    mountAll(master, [head, listBody]);
  }

  function renderListBody(listBody) {
    if (!plan()) { mountAll(listBody, [h('div', { class: 'tp-row-empty' }, 'Loading…')]); return; }
    const sel = state.getState().selection;
    let evs = (plan().events || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    if (search) {
      const q = search.toLowerCase();
      evs = evs.filter((e) => e.name.toLowerCase().includes(q) || (e.category || '').toLowerCase().includes(q));
    }
    if (!evs.length) { mountAll(listBody, [h('div', { class: 'tp-row-empty' }, 'No events yet')]); return; }
    const byCat = {};
    evs.forEach((e) => { (byCat[e.category || 'Uncategorized'] ||= []).push(e); });
    const nodes = [];
    Object.keys(byCat).sort().forEach((cat) => {
      nodes.push(h('div', { class: 'tp-grp' }, cat));
      byCat[cat].forEach((e) => {
        const n = e.properties.length;
        nodes.push(h('div', {
          class: 'tp-ev' + (sel.type === 'event' && sel.id === e.id ? ' is-active' : ''),
          onClick: () => selectEvent(e.id),
        },
          h('span', { class: 'tp-sd ' + dotColor(eventStatus(e)) }),
          h('div', { class: 'tp-ev-main' },
            h('div', { class: 'tp-ev-name' }, e.name),
            h('div', { class: 'tp-ev-sub' }, e.purpose || e.display_name || '—')),
          h('span', { class: 'tp-ev-meta' }, `${n} prop${n === 1 ? '' : 's'}`)));
      });
    });
    mountAll(listBody, nodes);
  }

  function dotColor(status) {
    if (status === 'verified') return 'green';
    if (status === 'implemented') return 'amber';
    return 'grey';
  }

  // Selecting a DIFFERENT event while dirty → confirm before discarding.
  function selectEvent(id) {
    if (id === editId) return;
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    state.setDirty(false);
    state.select('event', id);
  }

  async function newEvent() {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    state.setDirty(false);
    try {
      const r = await api.doAction('create_event', { name: 'New event' }, state.getState().branch);
      await state.reload();
      state.select('event', r.id);
      setTimeout(() => { const n = detail.querySelector('#ed-name'); if (n) { n.focus(); n.select(); } }, 0);
    } catch (err) {
      if (window.__tpBanner) window.__tpBanner((err && err.message) || 'Could not create event', 'err');
    }
  }

  // ============================ DETAIL / EDITOR ============================
  function renderDetail() {
    if (!plan()) { mountAll(detail, [h('div', { class: 'tp-empty' }, 'Loading…')]); return; }
    const e = curEvent();
    if (!e || !draft) {
      if (drawer) { drawer.destroy(); drawer = null; drawerEntityId = null; }
      mountAll(detail, [h('div', { class: 'tp-empty' }, 'Select an event, or create one.')]);
      return;
    }
    const nodes = [
      editorHead(e),
      h('div', { class: 'tp-ed-body' },
        detailsCard(e),
        propertiesCard(e),
        sourcesCard(e),
        destinationsCard(e)),
    ];
    mountAll(detail, nodes);

    // Recreate the comments drawer only when the selected event changes, so an
    // open Comments panel survives draft edits (which re-render the detail).
    if (drawerEntityId !== e.id) {
      if (drawer) { drawer.destroy(); drawer = null; }
      drawer = mountDrawer(document.querySelector('.tp-workspace') || document.body,
        { entityType: 'event', entityId: e.id, branch: state.getState().branch });
      drawerEntityId = e.id;
    }
  }

  // After mutating the draft, recompute dirty and re-render just the detail.
  function touch() { syncDirty(); renderDetail(); }

  // ---- sticky editor header: kicker + mono name + chips + actions + save cluster ----
  function editorHead(e) {
    const idBlock = h('div', { class: 'tp-ed-id' },
      h('div', { class: 'tp-ed-kicker' }, 'Event'),
      h('div', { class: 'tp-ed-name' }, draft.name || e.name));

    const chips = h('div', { class: 'tp-ed-chips' });
    if (draft.category) chips.appendChild(h('span', { class: 'tp-chip accent' }, draft.category));
    if (draft.trigger_type) {
      chips.appendChild(h('span', { class: 'tp-chip mono' },
        h('span', { class: 'tp-mono' }, draft.trigger_type)));
    }

    const actions = h('div', { class: 'tp-ed-actions' },
      h('button', { class: 'btn btn-ghost btn-sm', onClick: () => drawer && drawer.open() }, 'Comments'),
      h('button', { class: 'btn btn-ghost btn-sm', onClick: () => delEvent(e) }, 'Delete'),
      h('div', { class: 'tp-divv' }),
      saveClusterEl());

    return h('div', { class: 'tp-ed-head' }, h('div', { class: 'tp-ed-id-row' }, idBlock, chips, actions));
  }

  // Save cluster wired to this view's buffered-save flow.
  function saveClusterEl() {
    const box = h('div', { class: 'tp-savecluster' });
    if (dirty()) {
      box.appendChild(h('span', { class: 'tp-unsaved' }, '● Unsaved'));
      box.appendChild(h('button', {
        class: 'btn btn-ghost btn-sm', disabled: saving,
        onClick: () => { if (!saving) discard(); },
      }, 'Discard'));
      box.appendChild(h('button', {
        class: 'btn btn-primary btn-sm', disabled: saving,
        onClick: () => { if (!saving) doSave(); },
      }, saving ? 'Saving…' : 'Save changes'));
    } else {
      box.appendChild(h('span', { class: 'tp-saved-muted' }, 'Saved'));
      box.appendChild(h('button', { class: 'btn btn-primary btn-sm', disabled: true }, 'Save changes'));
    }
    return box;
  }

  function discard() {
    draft = clone(server);
    saving = false;
    syncDirty();
    renderDetail();
  }

  async function doSave() {
    const e = curEvent();
    if (!e) return;
    saving = true;
    renderDetail();
    try {
      await commitEvent(draft, server, e.id, state.getState().branch);
      await state.reload();
      // Re-snapshot from the fresh event (editId stays the same; onStateChange's
      // id-equality guard won't re-snapshot, so we do it here explicitly).
      const fresh = (plan().events || []).find((x) => x.id === e.id);
      if (fresh) {
        server = clone(snapshotOf(fresh));
        draft = clone(server);
      }
      saving = false;
      syncDirty();
      renderDetail();
      if (window.Fluxito && window.Fluxito.toast) window.Fluxito.toast('Saved', 'success');
    } catch (err) {
      saving = false;
      renderDetail(); // keep draft as-is so the user can retry
      if (window.__tpBanner) window.__tpBanner((err && err.message) || 'Save failed', 'err');
    }
  }

  // ---- Details card: description + 2-up grid of scalars ----
  function detailsCard(e) {
    const desc = h('textarea', {
      class: 'textarea', placeholder: 'When does this event fire?',
      onInput: () => { draft.description = desc.value; syncDirty(); refreshSaveCluster(); },
    });
    desc.value = draft.description || '';

    const cat = h('select', {
      class: 'select',
      onChange: () => { draft.category = cat.value; touch(); },
    },
      h('option', { value: '' }, '(no category)'),
      ...(plan().categories || []).map((c) =>
        h('option', { value: c.name, selected: draft.category === c.name }, c.name)));

    const trigger = h('input', {
      class: 'input mono', value: draft.trigger_type || '', placeholder: 'click / pageview / …',
      onInput: (ev) => { draft.trigger_type = ev.target.value; syncDirty(); refreshSaveCluster(); },
      onChange: () => touch(), // re-render so the header chip updates
    });

    // Text field bound to a draft scalar. mono=true for identifier-shaped values
    // (e.g. @handles). Header chips that mirror display_name/purpose don't exist,
    // so these use refreshSaveCluster() (no full re-render) to keep input focus.
    const field = (label, key, mono) => {
      const inp = h('input', {
        class: mono ? 'input mono' : 'input', value: draft[key] || '',
        onInput: (ev) => { draft[key] = ev.target.value; syncDirty(); refreshSaveCluster(); },
      });
      return h('div', { class: 'tp-field' }, h('label', { class: 'tp-lbl' }, label), inp);
    };
    const wrap = (label, control) => h('div', { class: 'tp-field' }, h('label', { class: 'tp-lbl' }, label), control);

    return card('Details', null, null,
      h('div', { class: 'tp-field', style: { marginBottom: '16px' } },
        h('label', { class: 'tp-lbl' }, 'Description'), desc),
      h('div', { class: 'tp-grid2' },
        field('Display name', 'display_name'),
        field('Purpose / KPI', 'purpose'),
        field('Business owner', 'owner_business'),
        field('Technical owner', 'owner_technical', true),
        wrap('Category', cat),
        wrap('Trigger', trigger)));
  }

  // ---- Properties card: data-table + add-property combobox ----
  function propertiesCard(e) {
    const tbody = h('tbody');
    draft.properties.forEach((p, idx) => tbody.appendChild(propRow(e, p, idx)));
    if (!draft.properties.length) {
      tbody.appendChild(h('tr', {},
        h('td', { class: 'tp-muted', colspan: '6', style: { padding: '14px 10px' } },
          'No properties yet — add one below.')));
    }
    const table = h('table', { class: 'tp-ptable' },
      h('thead', {}, h('tr', {},
        h('th', { style: { width: '18px' } }),
        h('th', {}, 'Name'),
        h('th', { style: { width: '110px' } }, 'Type'),
        h('th', { style: { width: '74px' } }, 'Required'),
        h('th', { style: { width: '170px' } }, 'Example'),
        h('th', {}, 'Description'))),
      tbody);

    const body = h('div', { class: 'tp-card-b' }, table, addPropertyCombo(e));
    return cardShell('Properties', String(draft.properties.length), null, body);
  }

  function propRow(e, p, idx) {
    const toggle = h('div', { class: 'tp-toggle' + (p.required ? ' on' : ''), role: 'switch' });
    toggle.addEventListener('click', () => { p.required = !p.required; touch(); });

    const ex = h('input', {
      class: 'tp-cellin mono', value: p.example || '', placeholder: 'example',
      onInput: (ev) => { p.example = ev.target.value; syncDirty(); refreshSaveCluster(); },
    });
    const ov = h('input', {
      class: 'tp-cellin', value: p.override_description || '', placeholder: 'override description',
      onInput: (ev) => { p.override_description = ev.target.value; syncDirty(); refreshSaveCluster(); },
    });

    const row = h('tr', { draggable: 'true', dataset: { name: p.name } },
      h('td', { class: 'tp-grip', title: 'Drag to reorder' }, '⠿'),
      h('td', {}, h('span', { class: 'tp-pn' }, p.name)),
      h('td', {}, h('span', { class: 'tp-badge ' + badgeClass(p.data_type) }, typeBadge(p.data_type, p.is_list))),
      h('td', {}, toggle),
      h('td', {}, ex),
      h('td', {},
        h('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
          ov,
          h('button', { class: 'btn btn-ghost btn-sm', title: 'Remove property',
            onClick: () => { draft.properties.splice(idx, 1); touch(); } }, '✕'))));
    wireRowDrag(row);
    return row;
  }

  // Drag-to-reorder rows; on drop reorder draft.properties (buffered — commits on Save).
  function wireRowDrag(row) {
    row.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.setData('text/plain', row.dataset.name);
      row.classList.add('is-dragging');
    });
    row.addEventListener('dragend', () => row.classList.remove('is-dragging'));
    row.addEventListener('dragover', (ev) => ev.preventDefault());
    row.addEventListener('drop', (ev) => {
      ev.preventDefault();
      const from = ev.dataTransfer.getData('text/plain');
      const to = row.dataset.name;
      if (from === to) return;
      const arr = draft.properties;
      const fi = arr.findIndex((x) => x.name === from);
      const ti = arr.findIndex((x) => x.name === to);
      if (fi < 0 || ti < 0) return;
      const [moved] = arr.splice(fi, 1);
      arr.splice(ti, 0, moved);
      touch();
    });
  }

  // Add-property combobox: search the event-property library or type a new name.
  function addPropertyCombo(e) {
    const inLib = (plan().properties && plan().properties.event) || [];
    const available = () => inLib.filter((lp) => !draft.properties.some((dp) => dp.name === lp.name));

    const input = h('input', {
      class: 'tp-combo-input',
      placeholder: 'Add a property — search the library or type a new name…',
    });
    const pop = h('div', { class: 'tp-combo-pop', style: { display: 'none' } });
    const wrap = h('div', { class: 'tp-combo' }, plusIcon(), input, pop);

    function addExisting(lp) {
      draft.properties.push({
        name: lp.name, data_type: lp.data_type, is_list: !!lp.is_list,
        required: false, example: '', override_description: '',
      });
      input.value = '';
      pop.style.display = 'none';
      touch();
    }
    // A brand-new (typed) property: mark __new so commit creates it (string by default).
    function addNew(name) {
      draft.properties.push({
        name, data_type: 'string', is_list: false,
        required: false, example: '', override_description: '', __new: true,
      });
      input.value = '';
      pop.style.display = 'none';
      touch();
    }

    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      const hits = available().filter((lp) => lp.name.toLowerCase().includes(q)).slice(0, 8);
      const opts = hits.map((lp) => h('div', {
        class: 'tp-combo-opt', onMousedown: (ev) => { ev.preventDefault(); addExisting(lp); },
      },
        h('span', { class: 'tp-pn' }, lp.name),
        h('span', { class: 'tp-badge ' + badgeClass(lp.data_type) }, typeBadge(lp.data_type, lp.is_list))));
      const exact = inLib.some((lp) => lp.name.toLowerCase() === q)
        || draft.properties.some((dp) => dp.name.toLowerCase() === q);
      if (q && !exact) {
        opts.push(h('div', {
          class: 'tp-combo-opt tp-combo-new', onMousedown: (ev) => { ev.preventDefault(); addNew(input.value.trim()); },
        }, `Create "${input.value.trim()}"`));
      }
      mountAll(pop, opts.length ? opts : [h('div', { class: 'tp-muted', style: { padding: '8px 10px' } }, 'No matches — type to create')]);
      pop.style.display = (opts.length || q) ? 'block' : 'none';
    });
    input.addEventListener('blur', () => setTimeout(() => { pop.style.display = 'none'; }, 150));
    return wrap;
  }

  // ---- Tracked on card: per-source status chips + Manage sources ----
  function sourcesCard(e) {
    const allSources = plan().sources || [];
    const body = h('div', { class: 'tp-card-b' });
    if (!allSources.length) {
      body.appendChild(h('div', { class: 'tp-muted' },
        'No sources defined — add them in Sources & Destinations.'));
      return cardShell('Tracked on', null, null, body);
    }
    const onByName = {};
    draft.sources.forEach((s) => { onByName[s.name] = s; });

    const srcs = h('div', { class: 'tp-srcs' });
    allSources.forEach((s) => {
      const cur = onByName[s.name];
      const on = !!cur;
      const dot = h('span', { class: 'tp-sd ' + (on ? statusDot(cur.implementation_status) : 'grey') });
      const name = h('span', { class: 'nm' }, s.name);
      const status = h('select', { class: 'tp-statusel st' },
        ...IMPL_STATUSES.map((x) => h('option', { value: x, selected: on && cur.implementation_status === x }, x)));
      status.disabled = !on;
      status.addEventListener('change', () => {
        const d = draft.sources.find((x) => x.name === s.name);
        if (d) { d.implementation_status = status.value; touch(); }
      });
      // Click the chip (not the select) toggles tracked on/off in the draft.
      const chip = h('div', { class: 'tp-src', style: { cursor: 'pointer', opacity: on ? '1' : '0.55' } },
        dot, name, status);
      chip.addEventListener('click', (ev) => {
        if (ev.target === status || status.contains(ev.target)) return;
        if (on) {
          draft.sources = draft.sources.filter((x) => x.name !== s.name);
        } else {
          draft.sources.push({ name: s.name, implementation_status: 'planned' });
        }
        touch();
      });
      srcs.appendChild(chip);
    });
    body.appendChild(srcs);

    const manage = h('button', { class: 'btn btn-secondary btn-sm',
      onClick: () => navigateAway('sources') }, 'Manage sources');
    const n = draft.sources.length;
    return cardShell('Tracked on', `${n} source${n === 1 ? '' : 's'}`, h('div', { class: 'tp-card-ha' }, manage), body);
  }

  function statusDot(status) {
    if (status === 'verified') return 'green';
    return 'amber'; // planned / implemented / deprecated → amber while tracked
  }

  // ---- Destinations card: mapping rows + map / unmap ----
  function destinationsCard(e) {
    const allDest = plan().destinations || [];
    const body = h('div', { class: 'tp-card-b', style: { paddingTop: '6px', paddingBottom: '6px' } });

    draft.destinations.forEach((d, idx) => {
      const mapInput = h('input', {
        class: 'tp-cellin mono', value: d.dest_event_name || '', placeholder: draft.name || e.name,
        style: { width: '180px' },
        onInput: (ev) => { d.dest_event_name = ev.target.value; syncDirty(); refreshSaveCluster(); },
      });
      const toggle = h('div', { class: 'tp-toggle' + (d.enabled ? ' on' : ''), role: 'switch', title: 'Enabled' });
      toggle.addEventListener('click', () => { d.enabled = !d.enabled; touch(); });
      body.appendChild(h('div', { class: 'tp-destrow' },
        h('span', { class: 'tp-dest-nm' }, d.destination),
        h('span', { class: 'tp-arrow' }, '→'),
        mapInput,
        h('div', { style: { marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '10px' } },
          toggle,
          h('button', { class: 'btn btn-ghost btn-sm', title: 'Unmap',
            onClick: () => { draft.destinations.splice(idx, 1); touch(); } }, '✕'))));
    });
    if (!draft.destinations.length) {
      body.appendChild(h('div', { class: 'tp-muted', style: { padding: '8px 0' } }, 'Not mapped to any destination.'));
    }

    // Map-destination control (only destinations not already mapped).
    let ha = null;
    const unmapped = allDest.filter((d) => !draft.destinations.some((dd) => dd.destination === d.name));
    if (unmapped.length) {
      const sel = h('select', { class: 'tp-statusel', style: { height: '28px' } },
        h('option', { value: '' }, '+ Map destination'),
        ...unmapped.map((d) => h('option', { value: d.name }, d.name)));
      sel.addEventListener('change', () => {
        if (!sel.value) return;
        draft.destinations.push({ destination: sel.value, dest_event_name: '', enabled: true });
        touch();
      });
      ha = h('div', { class: 'tp-card-ha' }, sel);
    }
    return cardShell('Destinations', null, ha, body);
  }

  // ---- delete (immediate; not part of buffered save) ----
  async function delEvent(e) {
    if (!confirm(`Delete event "${e.name}"?`)) return;
    try {
      await api.doAction('delete_event', { event_id: e.id }, state.getState().branch);
      editId = null; server = null; draft = null;
      state.setDirty(false);
      state.select(null, null);
      await state.reload();
      if (window.Fluxito && window.Fluxito.toast) window.Fluxito.toast('Event deleted', 'success');
    } catch (err) {
      if (window.__tpBanner) window.__tpBanner((err && err.message) || 'Delete failed', 'err');
    }
  }

  function navigateAway(view) {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    state.setDirty(false);
    state.setView(view);
  }

  // Re-render only the save cluster in place (avoids losing input focus on keystroke).
  function refreshSaveCluster() {
    const old = detail.querySelector('.tp-savecluster');
    if (old && old.parentNode) old.parentNode.replaceChild(saveClusterEl(), old);
  }

  // ----- small card helpers -----
  // card(): builds a full card whose body is the passed children inside .tp-card-b.
  function card(title, count, ha, ...children) {
    return cardShell(title, count, ha, h('div', { class: 'tp-card-b' }, ...children));
  }
  // cardShell(): body node is supplied by caller (already a .tp-card-b).
  function cardShell(title, count, ha, body) {
    const head = h('div', { class: 'tp-card-h' }, h('h3', {}, title));
    if (count != null) head.appendChild(h('span', { class: 'tp-ct' }, count));
    if (ha) head.appendChild(ha);
    return h('div', { class: 'tp-card' }, head, body);
  }

  return () => { unsub(); if (drawer) drawer.destroy(); };
}

// ============================ COMMIT (snapshot-sync) ============================
// Make the SERVER match the DRAFT for event `id` using idempotent / replace-all
// actions. Resolves property/source/destination NAME→id via the plan dict.
async function commitEvent(draft, server, id, branch) {
  const st = state.getState();
  const plan = st.plan;

  // 1) scalar fields — only the changed ones (category name → id).
  const scalarKeys = ['name', 'display_name', 'description', 'trigger_type', 'purpose', 'owner_business', 'owner_technical'];
  const patch = {};
  scalarKeys.forEach((k) => {
    const d = draft[k] || '';
    const s = server[k] || '';
    if (d !== s) patch[k] = d || null;
  });
  if ((draft.category || '') !== (server.category || '')) {
    patch.category_id = draft.category ? catByName(plan, draft.category) : null;
  }
  if (Object.keys(patch).length) {
    await api.doAction('update_event', { event_id: id, ...patch }, branch);
  }

  // 2) properties — UPSERT each draft prop (create typed-new first), detach removed.
  for (let i = 0; i < draft.properties.length; i++) {
    const p = draft.properties[i];
    let lib = propByName(plan, p.name);
    if (!lib || p.__new) {
      if (!lib) {
        const created = await api.doAction('create_property',
          { name: p.name, data_type: p.data_type || 'string', kind: 'event' }, branch);
        lib = { id: created.id };
      }
    }
    if (!lib || !lib.id) continue;
    await api.doAction('attach_property', {
      event_id: id, property_id: lib.id, sort_order: i,
      required: !!p.required, example: p.example || null, override_description: p.override_description || null,
    }, branch);
  }
  // detach any server property no longer in the draft
  const draftNames = new Set(draft.properties.map((p) => p.name));
  for (const sp of server.properties) {
    if (!draftNames.has(sp.name)) {
      const lib = propByName(plan, sp.name);
      if (lib) await api.doAction('detach_property', { event_id: id, property_id: lib.id }, branch);
    }
  }

  // 3) sources — replace-all (only if changed).
  const dSources = draft.sources.map((s) => ({ name: s.name, implementation_status: s.implementation_status }));
  const sSources = server.sources.map((s) => ({ name: s.name, implementation_status: s.implementation_status }));
  if (JSON.stringify(dSources) !== JSON.stringify(sSources)) {
    const sources = draft.sources
      .map((s) => { const lib = sourceByName(plan, s.name); return lib ? { source_id: lib.id, implementation_status: s.implementation_status } : null; })
      .filter(Boolean);
    await api.doAction('set_event_sources', { event_id: id, sources }, branch);
  }

  // 4) destinations — set each draft dest (idempotent), remove server dests absent from draft.
  for (const d of draft.destinations) {
    const lib = destByName(plan, d.destination);
    if (!lib) continue;
    const prev = server.destinations.find((x) => x.destination === d.destination);
    const changed = !prev || prev.dest_event_name !== d.dest_event_name || prev.enabled !== d.enabled;
    if (changed) {
      await api.doAction('set_event_destination', {
        event_id: id, destination_id: lib.id,
        dest_event_name: d.dest_event_name || null, enabled: !!d.enabled,
      }, branch);
    }
  }
  const draftDest = new Set(draft.destinations.map((d) => d.destination));
  for (const sd of server.destinations) {
    if (!draftDest.has(sd.destination)) {
      const lib = destByName(plan, sd.destination);
      if (lib) await api.doAction('remove_event_destination', { event_id: id, destination_id: lib.id }, branch);
    }
  }
}

// ----- tiny inline icons (mockup) -----
function searchIcon() {
  const svg = svgEl('0 0 24 24');
  svg.appendChild(pathEl('circle', { cx: '11', cy: '11', r: '7' }));
  svg.appendChild(pathEl('path', { d: 'M21 21l-4-4' }));
  return svg;
}
function plusIcon() {
  const svg = svgEl('0 0 24 24');
  svg.setAttribute('width', '14'); svg.setAttribute('height', '14');
  svg.appendChild(pathEl('path', { d: 'M12 5v14M5 12h14' }));
  return svg;
}
function svgEl(viewBox) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', viewBox);
  s.setAttribute('fill', 'none');
  s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '2');
  return s;
}
function pathEl(tag, attrs) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}
