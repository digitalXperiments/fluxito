// app/static/js/tracking_plan/views/sources.js
import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { banner } from 'tp/shell';
import { destByName } from 'tp/util/format';

export function mountView(container) {
  const host = h('div', { class: 'tp-detail' });
  mountAll(container, [host]);
  const unsub = state.subscribe(render);
  render();
  const plan = () => state.getState().plan;
  const run = async (action, params, ok) => { try { await api.doAction(action, params, state.getState().branch); if (ok) banner(ok, 'ok'); await state.reload(); } catch (e) { banner(e.message, 'err'); } };

  function sourceRow(s) {
    const chips = (s.destinations || []).map((dn) => h('span', { class: 'tp-chip' }, dn,
      h('button', { onClick: () => { const d = destByName(plan(), dn); if (d) run('disconnect_source_destination', { source_id: s.id, destination_id: d.id }); } }, '✕')));
    const route = h('select', h('option', { value: '' }, 'route to…'), ...(plan().destinations || []).map((d) => h('option', { value: d.id }, d.name)));
    route.onchange = () => { if (route.value) run('connect_source_destination', { source_id: s.id, destination_id: route.value }); };
    return h('div', { class: 'tp-ver-row' },
      h('div', { class: 'tp-ver-main' }, h('div', { class: 'tp-mono', style: { fontWeight: '600' } }, s.name),
        h('div', { class: 'tp-muted', style: { fontSize: '12px' } }, s.platform_type || '—'), h('div', { class: 'tp-tags' }, ...chips)),
      route,
      h('button', { class: 'btn btn-ghost btn-sm', onClick: () => { if (confirm('Delete source?')) run('delete_source', { source_id: s.id }); } }, 'Delete'));
  }
  function destRow(d) {
    return h('div', { class: 'tp-ver-row' },
      h('div', { class: 'tp-ver-main' }, h('div', { class: 'tp-mono', style: { fontWeight: '600' } }, d.name),
        h('div', { class: 'tp-muted', style: { fontSize: '12px' } }, `${d.platform}${d.platform_account_id ? ' · ' + d.platform_account_id : ''}`)),
      h('button', { class: 'btn btn-ghost btn-sm', onClick: () => { if (confirm('Delete destination?')) run('delete_destination', { destination_id: d.id }); } }, 'Delete'));
  }

  function render() {
    const srcRows = (plan().sources || []).map(sourceRow);
    if (!srcRows.length) srcRows.push(h('div', { class: 'tp-row-empty' }, 'No sources yet.'));
    const destRows = (plan().destinations || []).map(destRow);
    if (!destRows.length) destRows.push(h('div', { class: 'tp-row-empty' }, 'No destinations yet.'));
    const newSource = h('button', { class: 'btn btn-primary btn-sm', onClick: () => { const n = prompt('Source name:'); if (!n) return; run('create_source', { name: n, platform_type: prompt('Platform type (web/ios/android/server/warehouse)?') || null }); } }, '+ Source');
    const newDest = h('button', { class: 'btn btn-primary btn-sm', onClick: () => { const n = prompt('Destination name:'); if (!n) return; const pl = prompt('Platform (ga4/amplitude/mixpanel/…)?'); if (!pl) return; run('create_destination', { name: n, platform: pl, platform_account_id: prompt('Account id (optional)?') || null }); } }, '+ Destination');
    mountAll(host, [h('div', { class: 'tp-detail-inner' },
      h('div', { class: 'tp-d-head' }, h('div', { class: 'tp-d-title' }, h('h2', {}, 'Sources')), h('div', { class: 'tp-d-actions' }, newSource)),
      h('div', { class: 'tp-section' }, ...srcRows),
      h('div', { class: 'tp-d-head', style: { marginTop: '20px' } }, h('div', { class: 'tp-d-title' }, h('h2', {}, 'Destinations')), h('div', { class: 'tp-d-actions' }, newDest)),
      h('div', { class: 'tp-section' }, ...destRows))]);
  }
  return unsub;
}
