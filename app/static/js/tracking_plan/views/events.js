// app/static/js/tracking_plan/views/events.js
// Avo-grade Events view: list (grouped by category, searchable, status dots) +
// single-scroll editor. mountView(container) subscribes to state and re-renders.

import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { banner } from 'tp/shell';
import { mountDrawer } from 'tp/comments';
import {
  eventStatus, eventProps, propByName, sourceByName, destByName, catByName, typeBadge,
} from 'tp/util/format';

export function mountView(container) {
  const layout = h('div', { class: 'tp-master-detail' });
  const master = h('div', { class: 'tp-master' });
  const detail = h('div', { class: 'tp-detail', id: 'tp-ev-detail' });
  layout.appendChild(master);
  layout.appendChild(detail);
  mountAll(container, [layout]);

  let search = '';
  let drawer = null;

  const unsub = state.subscribe(() => { renderList(); renderDetail(); });
  renderList();
  renderDetail();

  function plan() { return state.getState().plan; }

  function renderList() {
    const head = h('div', { class: 'tp-master-head' },
      h('div', { class: 'tp-search' },
        h('input', { placeholder: 'Search events', value: search,
          onInput: (e) => { search = e.target.value; renderListBody(listBody); } })),
      h('button', { class: 'btn btn-primary btn-sm btn-block', onClick: newEvent }, '+ New event'));
    const listBody = h('div', { class: 'tp-master-list' });
    renderListBody(listBody);
    mountAll(master, [head, listBody]);
  }

  function renderListBody(listBody) {
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
      nodes.push(h('div', { class: 'tp-cat-label' }, cat));
      byCat[cat].forEach((e) => {
        nodes.push(h('div', {
          class: 'tp-row' + (sel.type === 'event' && sel.id === e.id ? ' is-active' : ''),
          onClick: () => selectEvent(e.id),
        },
          h('span', { class: 'tp-status-dot', dataset: { s: eventStatus(e) } }),
          h('div', { class: 'tp-row-main' },
            h('div', { class: 'tp-name' }, e.name),
            h('div', { class: 'tp-row-sub' }, e.purpose || e.display_name || '—')),
          h('div', { class: 'tp-row-meta' }, `${e.properties.length}p`)));
      });
    });
    mountAll(listBody, nodes);
  }

  function selectEvent(id) {
    if (state.getState().dirty && !confirm('Discard unsaved changes?')) return;
    state.setDirty(false);
    state.select('event', id);
  }

  async function newEvent() {
    try {
      const r = await api.doAction('create_event', { name: 'New event' }, state.getState().branch);
      await state.reload();              // <-- structural bug fix: list refreshes from server
      state.select('event', r.id);       // open straight into the editor
      setTimeout(() => { const n = detail.querySelector('#ed-name'); if (n) { n.focus(); n.select(); } }, 0);
    } catch (e) { banner(e.message, 'err'); }
  }

  function renderDetail() {
    const sel = state.getState().selection;
    const e = sel.type === 'event' ? (plan().events || []).find((x) => x.id === sel.id) : null;
    if (drawer) { drawer.destroy(); drawer = null; }
    if (!e) {
      mountAll(detail, [h('div', { class: 'tp-empty' }, 'Select an event, or create one.')]);
      return;
    }
    const inner = h('div', { class: 'tp-detail-inner' });
    inner.appendChild(headerSection(e));
    inner.appendChild(metaSection(e));
    inner.appendChild(propertiesSection(e));
    inner.appendChild(sourcesSection(e));
    inner.appendChild(destSection(e));
    mountAll(detail, [inner]);
    // drawer mounts into the workspace root (fixed-position), toggled by the header button
    drawer = mountDrawer(document.querySelector('.tp-workspace') || document.body,
      { entityType: 'event', entityId: e.id, branch: state.getState().branch });
  }

  // ---- header: inline name, category, trigger badge, tags, comments, ⋯ ----
  function headerSection(e) {
    const name = h('input', { class: 'tp-titlefield', id: 'ed-name', value: e.name,
      onChange: () => save(e, { name: name.value.trim() }) });
    const cat = h('select', { onChange: () => save(e, { category_id: catByName(plan(), cat.value) }) },
      h('option', { value: '' }, '(no category)'),
      ...(plan().categories || []).map((c) => h('option', { value: c.name, selected: e.category === c.name }, c.name)));
    const trigger = h('input', { class: 'tp-mono-input', value: e.trigger_type || '', placeholder: 'click / pageview / …',
      onChange: (ev) => save(e, { trigger_type: ev.target.value || null }) });

    const tagsWrap = h('div', { class: 'tp-tags' });
    let tags = (e.tags || []).slice();
    const tagInput = h('input', { placeholder: 'add tag…',
      onKeydown: (ev) => { if (ev.key === 'Enter' && tagInput.value.trim()) { tags.push(tagInput.value.trim()); tagInput.value = ''; renderTags(); save(e, { tags }); } } });
    function renderTags() {
      mountAll(tagsWrap, [
        ...tags.map((t, i) => h('span', { class: 'tp-chip' }, t,
          h('button', { onClick: () => { tags.splice(i, 1); renderTags(); save(e, { tags }); } }, '✕'))),
        tagInput,
      ]);
    }
    renderTags();

    const commentsBtn = h('button', { class: 'btn btn-ghost btn-sm', onClick: () => drawer && drawer.open() }, '💬 Comments');
    const moreBtn = h('button', { class: 'btn btn-ghost btn-sm', onClick: () => delEvent(e) }, 'Delete');

    return h('div', { class: 'tp-d-head' },
      h('div', { class: 'tp-d-title' }, name),
      h('div', { class: 'tp-d-actions' }, commentsBtn, moreBtn),
      h('div', { class: 'tp-d-subrow' }, cat, trigger, tagsWrap));
  }

  // ---- description (autosave-on-blur) + meta grid ----
  function metaSection(e) {
    const desc = h('textarea', { id: 'ed-desc', placeholder: 'Description — when does this fire?',
      onInput: () => state.setDirty(true), onBlur: () => save(e, { description: desc.value || null }) });
    desc.value = e.description || '';
    const field = (label, key, val, mono) => {
      const inp = h('input', { class: mono ? 'tp-mono-input' : '', value: val || '',
        onBlur: () => save(e, { [key]: inp.value || null }), onInput: () => state.setDirty(true) });
      return h('div', { class: 'tp-field' }, h('label', {}, label), inp);
    };
    return h('div', { class: 'tp-section' },
      h('div', { class: 'tp-field tp-col-2' }, h('label', {}, 'Description'), desc),
      h('div', { class: 'tp-fieldgrid' },
        field('Display name', 'display_name', e.display_name),
        field('Purpose / KPI', 'purpose', e.purpose),
        field('Business owner', 'owner_business', e.owner_business),
        field('Technical owner', 'owner_technical', e.owner_technical)));
  }

  // ---- event properties table (drag-reorder + inline + combobox) ----
  function propertiesSection(e) {
    const sec = h('div', { class: 'tp-section' },
      h('h3', {}, 'Properties ', h('span', { class: 'tp-sec-count' }, e.properties.length)));
    const tbody = h('tbody');
    const order = e.properties.map((p) => p.name); // current order (already sort_order-sorted)

    e.properties.forEach((p, idx) => {
      const reqBox = h('input', { type: 'checkbox', checked: p.required,
        onChange: () => attach(e, p, idx, { required: reqBox.checked }) });
      const exInput = h('input', { class: 'tp-cell-input', value: p.example || '', placeholder: 'example',
        onChange: () => attach(e, p, idx, { example: exInput.value || null }) });
      const ovInput = h('input', { class: 'tp-cell-input', value: p.override_description || '', placeholder: 'override description',
        onChange: () => attach(e, p, idx, { override_description: ovInput.value || null }) });
      const row = h('tr', { class: 'tp-prow', draggable: 'true', dataset: { name: p.name, idx } },
        h('td', { class: 'tp-drag' }, '⋮⋮'),
        h('td', { class: 'tp-pname' }, p.name),
        h('td', {}, h('span', { class: 'tp-typebadge' }, typeBadge(p.data_type, p.is_list))),
        h('td', {}, reqBox),
        h('td', {}, exInput),
        h('td', {}, ovInput),
        h('td', { class: 'tp-cell-act' }, h('button', { class: 'btn btn-ghost btn-sm', onClick: () => detach(e, p) }, 'Remove')));
      wireDrag(row, e, order);
      tbody.appendChild(row);
    });
    if (!e.properties.length) tbody.appendChild(h('tr', {}, h('td', { class: 'tp-muted', colspan: '7', style: { padding: '14px' } }, 'No properties attached.')));

    sec.appendChild(h('table', { class: 'tp-itable' },
      h('thead', {}, h('tr', {}, h('th', {}), h('th', {}, 'Name'), h('th', {}, 'Type'), h('th', {}, 'Req'), h('th', {}, 'Example'), h('th', {}, 'Override'), h('th', {}))),
      tbody));
    sec.appendChild(addPropertyCombobox(e));
    return sec;
  }

  // Drag-to-reorder: on drop, recompute order and re-issue attach_property with
  // new 0-based sort_order for each affected row (preserving required/example/override).
  function wireDrag(row, e, order) {
    row.addEventListener('dragstart', (ev) => { ev.dataTransfer.setData('text/plain', row.dataset.name); row.classList.add('is-dragging'); });
    row.addEventListener('dragend', () => row.classList.remove('is-dragging'));
    row.addEventListener('dragover', (ev) => ev.preventDefault());
    row.addEventListener('drop', async (ev) => {
      ev.preventDefault();
      const from = ev.dataTransfer.getData('text/plain');
      const to = row.dataset.name;
      if (from === to) return;
      const arr = order.slice();
      arr.splice(arr.indexOf(from), 1);
      arr.splice(arr.indexOf(to), 0, from);
      try {
        for (let i = 0; i < arr.length; i++) {
          const cur = e.properties.find((p) => p.name === arr[i]);
          const lib = propByName(plan(), arr[i]);
          if (!lib) continue;
          await api.doAction('attach_property', {
            event_id: e.id, property_id: lib.id, sort_order: i,
            required: cur.required, example: cur.example, override_description: cur.override_description,
          }, state.getState().branch);
        }
        await state.reload();
      } catch (err) { banner(err.message, 'err'); }
    });
  }

  function addPropertyCombobox(e) {
    const lib = eventProps(plan()).filter((p) => !e.properties.some((ep) => ep.name === p.name));
    const input = h('input', { class: 'tp-combo-input', placeholder: 'Add property — search library or type a new name' });
    const pop = h('div', { class: 'tp-combo-pop', style: { display: 'none' } });
    const wrap = h('div', { class: 'tp-combo' }, input, pop);
    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      const hits = lib.filter((p) => p.name.toLowerCase().includes(q)).slice(0, 8);
      const opts = hits.map((p) => h('div', { class: 'tp-combo-opt', onClick: () => attachExisting(e, p) },
        p.name, h('span', { class: 'tp-typebadge' }, typeBadge(p.data_type, p.is_list))));
      if (q && !lib.some((p) => p.name.toLowerCase() === q)) {
        opts.push(h('div', { class: 'tp-combo-opt tp-combo-new', onClick: () => createAndAttach(e, input.value.trim()) }, `Create "${input.value.trim()}"`));
      }
      mountAll(pop, opts.length ? opts : [h('div', { class: 'tp-muted', style: { padding: '8px 10px' } }, 'No matches — type to create')]);
      pop.style.display = opts.length || q ? 'block' : 'none';
    });
    input.addEventListener('blur', () => setTimeout(() => { pop.style.display = 'none'; }, 150));
    return wrap;
  }

  async function attachExisting(e, libProp) {
    try {
      await api.doAction('attach_property', { event_id: e.id, property_id: libProp.id, sort_order: e.properties.length }, state.getState().branch);
      await state.reload();
    } catch (err) { banner(err.message, 'err'); }
  }
  async function createAndAttach(e, name) {
    try {
      const cp = await api.doAction('create_property', { name, data_type: 'string', kind: 'event' }, state.getState().branch);
      await api.doAction('attach_property', { event_id: e.id, property_id: cp.id, sort_order: e.properties.length }, state.getState().branch);
      await state.reload();
    } catch (err) { banner(err.message, 'err'); }
  }
  async function attach(e, p, idx, patch) {
    const lib = propByName(plan(), p.name); if (!lib) return;
    try {
      await api.doAction('attach_property', {
        event_id: e.id, property_id: lib.id, sort_order: idx,
        required: p.required, example: p.example, override_description: p.override_description, ...patch,
      }, state.getState().branch);
      await state.reload();
    } catch (err) { banner(err.message, 'err'); }
  }
  async function detach(e, p) {
    const lib = propByName(plan(), p.name); if (!lib) return;
    try { await api.doAction('detach_property', { event_id: e.id, property_id: lib.id }, state.getState().branch); await state.reload(); }
    catch (err) { banner(err.message, 'err'); }
  }

  // ---- Tracked on: per-source status chips ----
  function sourcesSection(e) {
    const sec = h('div', { class: 'tp-section' }, h('h3', {}, 'Tracked on'));
    if (!(plan().sources || []).length) {
      sec.appendChild(h('div', { class: 'tp-muted' }, 'No sources defined — add them in Sources & Destinations.'));
      return sec;
    }
    const cur = {}; (e.sources || []).forEach((s) => { cur[s.name] = s.implementation_status; });
    const wrap = h('div', { class: 'tp-src-list' });
    (plan().sources || []).forEach((s) => {
      const on = s.name in cur;
      const box = h('input', { type: 'checkbox', checked: on });
      const sel = h('select', ...['planned', 'implemented', 'verified', 'deprecated'].map((x) => h('option', { selected: cur[s.name] === x }, x)));
      wrap.appendChild(h('label', { class: 'tp-src-toggle' + (on ? ' is-on' : ''), dataset: { sid: s.id } }, box, h('span', { class: 'tp-src-name' }, s.name), sel));
    });
    const saveBtn = h('button', { class: 'btn btn-secondary btn-sm', style: { marginTop: '10px' },
      onClick: async () => {
        const sources = [...wrap.querySelectorAll('.tp-src-toggle')]
          .filter((l) => l.querySelector('input').checked)
          .map((l) => ({ source_id: l.dataset.sid, implementation_status: l.querySelector('select').value }));
        try { await api.doAction('set_event_sources', { event_id: e.id, sources }, state.getState().branch); await state.reload(); }
        catch (err) { banner(err.message, 'err'); }
      } }, 'Update sources');
    sec.appendChild(wrap); sec.appendChild(saveBtn);
    return sec;
  }

  // ---- Destinations mapping rows ----
  function destSection(e) {
    const sec = h('div', { class: 'tp-section' }, h('h3', {}, 'Destinations'));
    const tbody = h('tbody');
    (e.destinations || []).forEach((dd) => {
      tbody.appendChild(h('tr', {},
        h('td', { class: 'tp-pname' }, dd.destination),
        h('td', { class: 'tp-mono' }, dd.dest_event_name || e.name),
        h('td', { class: 'tp-cell-act' }, h('button', { class: 'btn btn-ghost btn-sm',
          onClick: async () => { const d = destByName(plan(), dd.destination); if (d) { try { await api.doAction('remove_event_destination', { event_id: e.id, destination_id: d.id }, state.getState().branch); await state.reload(); } catch (err) { banner(err.message, 'err'); } } } }, 'Remove'))));
    });
    if (!(e.destinations || []).length) tbody.appendChild(h('tr', {}, h('td', { class: 'tp-muted', colspan: '3', style: { padding: '14px' } }, 'Not mapped to any destination.')));
    sec.appendChild(h('table', { class: 'tp-itable' }, h('thead', {}, h('tr', {}, h('th', {}, 'Destination'), h('th', {}, 'Maps to'), h('th', {}))), tbody));
    if ((plan().destinations || []).length) {
      const sel = h('select', ...(plan().destinations || []).map((d) => h('option', { value: d.id }, d.name)));
      const nameIn = h('input', { class: 'tp-mono-input', placeholder: 'dest event name (optional)' });
      sec.appendChild(h('div', { class: 'tp-inline-add' }, sel, nameIn,
        h('button', { class: 'btn btn-secondary btn-sm',
          onClick: async () => { try { await api.doAction('set_event_destination', { event_id: e.id, destination_id: sel.value, dest_event_name: nameIn.value || null }, state.getState().branch); await state.reload(); } catch (err) { banner(err.message, 'err'); } } }, 'Map')));
    }
    return sec;
  }

  async function save(e, patch) {
    try {
      await api.doAction('update_event', { event_id: e.id, ...patch }, state.getState().branch);
      state.setDirty(false);
      await state.reload();
    } catch (err) { banner(err.message, 'err'); }
  }
  async function delEvent(e) {
    if (!confirm(`Delete event "${e.name}"?`)) return;
    try { await api.doAction('delete_event', { event_id: e.id }, state.getState().branch); state.select(null, null); await state.reload(); }
    catch (err) { banner(err.message, 'err'); }
  }

  return () => { unsub(); if (drawer) drawer.destroy(); };
}
