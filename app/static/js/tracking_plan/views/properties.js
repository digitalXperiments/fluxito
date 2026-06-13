// app/static/js/tracking_plan/views/properties.js
import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { banner } from 'tp/shell';
import { mountDrawer } from 'tp/comments';
import { allProps, typeBadge } from 'tp/util/format';

export function mountView(container) {
  const master = h('div', { class: 'tp-master' });
  const detail = h('div', { class: 'tp-detail' });
  mountAll(container, [h('div', { class: 'tp-master-detail' }, master, detail)]);
  let search = '';
  let drawer = null;
  const unsub = state.subscribe(() => { renderList(); renderDetail(); });
  renderList(); renderDetail();
  const plan = () => state.getState().plan;

  function renderList() {
    if (!plan()) { mountAll(master, [h('div', { class: 'tp-row-empty' }, 'Loading…')]); return; }
    const head = h('div', { class: 'tp-master-head' },
      h('div', { class: 'tp-search' }, h('input', { placeholder: 'Search properties', value: search, onInput: (e) => { search = e.target.value; renderList(); } })),
      h('button', { class: 'btn btn-primary btn-sm btn-block', onClick: create }, '+ New property'));
    const list = h('div', { class: 'tp-master-list' });
    const sel = state.getState().selection;
    const kinds = [['event', 'Event'], ['user', 'User'], ['group', 'Group'], ['system', 'System']];
    const nodes = [];
    kinds.forEach(([k, label]) => {
      let items = (plan().properties[k] || []);
      if (search) items = items.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()));
      if (!items.length) return;
      nodes.push(h('div', { class: 'tp-cat-label' }, label + ' properties'));
      items.forEach((p) => nodes.push(h('div', { class: 'tp-row' + (sel.type === 'property' && sel.id === p.id ? ' is-active' : ''), onClick: () => state.select('property', p.id) },
        h('div', { class: 'tp-row-main' }, h('div', { class: 'tp-name' }, p.name), h('div', { class: 'tp-row-sub' }, p.description || '—')),
        h('div', { class: 'tp-row-meta' }, typeBadge(p.data_type, p.is_list)))));
    });
    if (!nodes.length) nodes.push(h('div', { class: 'tp-row-empty' }, 'No properties'));
    mountAll(list, nodes);
    mountAll(master, [head, list]);
  }

  async function create() {
    const name = prompt('Property name:'); if (!name) return;
    try { const r = await api.doAction('create_property', { name, data_type: 'string', kind: 'event' }, state.getState().branch); await state.reload(); state.select('property', r.id); }
    catch (e) { banner(e.message, 'err'); }
  }

  function renderDetail() {
    if (!plan()) { mountAll(detail, [h('div', { class: 'tp-empty' }, 'Loading…')]); return; }
    const sel = state.getState().selection;
    const p = sel.type === 'property' ? allProps(plan()).find((x) => x.id === sel.id) : null;
    if (drawer) { drawer.destroy(); drawer = null; }
    if (!p) { mountAll(detail, [h('div', { class: 'tp-empty' }, 'Select a property to edit its type & constraints.')]); return; }
    const c = p.constraints || {};
    const nameI = h('input', { class: 'tp-titlefield', value: p.name });
    const kindS = h('select', {}, ...['event', 'user', 'group', 'system'].map((k) => h('option', { selected: p.kind === k }, k)));
    const typeS = h('select', {}, ...['string', 'int', 'float', 'boolean', 'object', 'array'].map((t) => h('option', { selected: p.data_type === t }, t)));
    const listC = h('input', { type: 'checkbox', checked: p.is_list });
    const piiC = h('input', { type: 'checkbox', checked: p.is_pii });
    const descT = h('textarea', {}); descT.value = p.description || '';
    const enumI = h('input', { class: 'tp-mono-input', value: (c.allowed_values || []).join(', ') });
    const minI = h('input', { type: 'number', value: c.min ?? '' });
    const maxI = h('input', { type: 'number', value: c.max ?? '' });
    const rxI = h('input', { class: 'tp-mono-input', value: c.regex || '' });
    const save = h('button', { class: 'btn btn-primary btn-sm', onClick: async () => {
      const cons = {};
      const ev = enumI.value.split(',').map((s) => s.trim()).filter(Boolean);
      if (ev.length) cons.allowed_values = ev;
      if (minI.value !== '') cons.min = Number(minI.value);
      if (maxI.value !== '') cons.max = Number(maxI.value);
      if (rxI.value.trim()) cons.regex = rxI.value.trim();
      try { await api.doAction('update_property', { property_id: p.id, name: nameI.value.trim(), data_type: typeS.value, is_list: listC.checked, is_pii: piiC.checked, description: descT.value || null, constraints: Object.keys(cons).length ? cons : null }, state.getState().branch); banner('Property saved', 'ok'); await state.reload(); }
      catch (e) { banner(e.message, 'err'); }
    } }, 'Save');
    const del = h('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { if (!confirm(`Delete property "${p.name}"?`)) return; try { await api.doAction('delete_property', { property_id: p.id }, state.getState().branch); state.select(null, null); await state.reload(); } catch (e) { banner(e.message, 'err'); } } }, 'Delete');
    const field = (label, node) => h('div', { class: 'tp-field' }, h('label', {}, label), node);
    mountAll(detail, [h('div', { class: 'tp-detail-inner' },
      h('div', { class: 'tp-d-head' }, h('div', { class: 'tp-d-title' }, nameI), h('div', { class: 'tp-d-actions' }, del, save)),
      h('div', { class: 'tp-section' }, h('div', { class: 'tp-fieldgrid' }, field('Kind', kindS), field('Data type', typeS), field('List', listC), field('PII', piiC), h('div', { class: 'tp-field tp-col-2' }, h('label', {}, 'Description'), descT))),
      h('div', { class: 'tp-section' }, h('h3', {}, 'Constraints'), h('div', { class: 'tp-fieldgrid' }, h('div', { class: 'tp-field tp-col-2' }, h('label', {}, 'Allowed values (enum, comma-separated)'), enumI), field('Min', minI), field('Max', maxI), h('div', { class: 'tp-field tp-col-2' }, h('label', {}, 'Regex / format'), rxI))))]);
    drawer = mountDrawer(document.querySelector('.tp-workspace') || document.body, { entityType: 'property', entityId: p.id, branch: state.getState().branch });
  }
  return () => { unsub(); if (drawer) drawer.destroy(); };
}
