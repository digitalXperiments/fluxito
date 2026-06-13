// app/static/js/tracking_plan/views/bundles.js
import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { banner } from 'tp/shell';

export function mountView(container) {
  const host = h('div', { class: 'tp-detail' });
  mountAll(container, [host]);
  const plan = () => state.getState().plan;
  const unsub = state.subscribe(render);
  render();

  async function manage(b) {
    const cur = b.properties.map((x) => x.name).join(', ');
    const pick = prompt(`Bundle "${b.name}" — property name to ADD (current: ${cur || 'none'}):`); if (!pick) return;
    const pr = (plan().properties.event || []).find((x) => x.name === pick.trim());
    if (!pr) { banner('No event property named ' + pick, 'err'); return; }
    try { await api.doAction('add_property_to_bundle', { bundle_id: b.id, property_id: pr.id }, state.getState().branch); await state.reload(); }
    catch (e) { banner(e.message, 'err'); }
  }

  function render() {
    if (!plan()) { mountAll(host, [h('div', { class: 'tp-row-empty' }, 'Loading…')]); return; }
    const rows = (plan().bundles || []).map((b) => h('div', { class: 'tp-ver-row' },
      h('div', { class: 'tp-ver-num' }, b.properties.length),
      h('div', { class: 'tp-ver-main' }, h('div', { class: 'tp-mono', style: { fontWeight: '600' } }, b.name),
        h('div', { class: 'tp-muted', style: { fontSize: '12px' } }, `${b.description || ''} · ${b.properties.map((x) => x.name).join(', ') || 'no properties'}`)),
      h('button', { class: 'btn btn-ghost btn-sm', onClick: () => manage(b) }, 'Manage'),
      h('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { if (!confirm('Delete bundle?')) return; try { await api.doAction('delete_bundle', { bundle_id: b.id }, state.getState().branch); await state.reload(); } catch (e) { banner(e.message, 'err'); } } }, 'Delete')));
    if (!rows.length) rows.push(h('div', { class: 'tp-row-empty' }, 'No bundles. Bundles attach a group of properties to events at once.'));
    const create = h('button', { class: 'btn btn-primary btn-sm', onClick: async () => { const n = prompt('Bundle name:'); if (!n) return; try { await api.doAction('create_bundle', { name: n }, state.getState().branch); await state.reload(); } catch (e) { banner(e.message, 'err'); } } }, '+ New');
    mountAll(host, [h('div', { class: 'tp-detail-inner' },
      h('div', { class: 'tp-d-head' }, h('div', { class: 'tp-d-title' }, h('h2', {}, 'Property bundles')), h('div', { class: 'tp-d-actions' }, create)),
      h('div', { class: 'tp-section' }, ...rows))]);
  }
  return unsub;
}
