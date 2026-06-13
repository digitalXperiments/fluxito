// app/static/js/tracking_plan/views/metrics.js
import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { banner } from 'tp/shell';
import { eventByName } from 'tp/util/format';

export function mountView(container) {
  const host = h('div', { class: 'tp-detail' });
  mountAll(container, [host]);
  const plan = () => state.getState().plan;
  const unsub = state.subscribe(render);
  render();

  function render() {
    if (!plan()) { mountAll(host, [h('div', { class: 'tp-row-empty' }, 'Loading…')]); return; }
    const rows = (plan().metrics || []).map((m) => h('div', { class: 'tp-ver-row' },
      h('div', { class: 'tp-ver-num', style: { fontSize: '11px', textTransform: 'uppercase' } }, m.type),
      h('div', { class: 'tp-ver-main' }, h('div', { class: 'tp-mono', style: { fontWeight: '600' } }, m.name),
        h('div', { class: 'tp-muted', style: { fontSize: '12px' } }, `${m.event || '—'} ${m.description || ''}`)),
      h('button', { class: 'btn btn-ghost btn-sm', onClick: async () => { if (!confirm('Delete metric?')) return; try { await api.doAction('delete_metric', { metric_id: m.id }, state.getState().branch); await state.reload(); } catch (e) { banner(e.message, 'err'); } } }, 'Delete')));
    if (!rows.length) rows.push(h('div', { class: 'tp-row-empty' }, 'No metrics yet.'));
    const create = h('button', { class: 'btn btn-primary btn-sm', onClick: async () => {
      const n = prompt('Metric name:'); if (!n) return;
      const ty = prompt('Type (count/sum/unique/average/ratio)?', 'count') || 'count';
      const evn = prompt('Event name (optional)?');
      const ev = evn ? eventByName(plan(), evn.trim()) : null;
      try { await api.doAction('create_metric', { name: n, type: ty, event_id: ev ? ev.id : null }, state.getState().branch); await state.reload(); }
      catch (e) { banner(e.message, 'err'); }
    } }, '+ New');
    mountAll(host, [h('div', { class: 'tp-detail-inner' },
      h('div', { class: 'tp-d-head' }, h('div', { class: 'tp-d-title' }, h('h2', {}, 'Metrics')), h('div', { class: 'tp-d-actions' }, create)),
      h('div', { class: 'tp-section' }, ...rows))]);
  }
  return unsub;
}
