// app/static/js/tracking_plan/views/categories.js
import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { banner } from 'tp/shell';

export function mountView(container) {
  const host = h('div', { class: 'tp-detail' });
  mountAll(container, [host]);
  const unsub = state.subscribe(render);
  render();
  const plan = () => state.getState().plan;

  function count(cat) { return (plan().events || []).filter((e) => e.category === cat.name).length; }

  function render() {
    const rows = (plan().categories || []).map((c) => {
      const nameI = h('input', { value: c.name, class: 'tp-mono-input' });
      const colorI = h('input', { type: 'color', value: c.color || '#888888' });
      return h('div', { class: 'tp-ver-row' },
        h('div', { class: 'tp-ver-main' }, nameI, h('div', { class: 'tp-muted', style: { fontSize: '12px' } }, `${count(c)} events`)),
        colorI,
        h('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { try { await api.doAction('update_category', { category_id: c.id, name: nameI.value.trim(), color: colorI.value }, state.getState().branch); banner('Category saved', 'ok'); await state.reload(); } catch (e) { banner(e.message, 'err'); } } }, 'Save'),
        h('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { if (!confirm(`Delete "${c.name}"? Events in it become uncategorized.`)) return; try { await api.doAction('delete_category', { category_id: c.id }, state.getState().branch); await state.reload(); } catch (e) { banner(e.message, 'err'); } } }, 'Delete'));
    });
    if (!rows.length) rows.push(h('div', { class: 'tp-row-empty' }, 'No categories yet.'));
    const create = h('button', { class: 'btn btn-primary btn-sm', onClick: async () => { const n = prompt('Category name:'); if (!n) return; try { await api.doAction('create_category', { name: n }, state.getState().branch); await state.reload(); } catch (e) { banner(e.message, 'err'); } } }, '+ New category');
    mountAll(host, [h('div', { class: 'tp-detail-inner' },
      h('div', { class: 'tp-d-head' }, h('div', { class: 'tp-d-title' }, h('h2', {}, 'Categories')), h('div', { class: 'tp-d-actions' }, create)),
      h('div', { class: 'tp-section' }, ...rows))]);
  }
  return unsub;
}
