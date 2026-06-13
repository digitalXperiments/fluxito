// app/static/js/tracking_plan/views/versions.js
import { h, mountAll } from 'tp/render';
import * as api from 'tp/api';

export function mountView(container) {
  const host = h('div', { class: 'tp-detail-inner' });
  mountAll(container, [host]);
  load();
  async function load() {
    mountAll(host, [h('div', { class: 'tp-muted' }, 'Loading…')]);
    let vs = [];
    try { vs = (await api.versions()).versions || []; } catch (e) {}
    const nodes = [h('div', { class: 'tp-d-head', style: { marginBottom: '16px' } }, h('div', { class: 'tp-d-title' }, h('h2', {}, 'Published versions')))];
    if (!vs.length) { nodes.push(h('div', { class: 'tp-row-empty' }, 'Nothing published yet. Use Publish on the main branch.')); mountAll(host, nodes); return; }
    vs.forEach((v, i) => {
      const row = h('div', { class: 'tp-ver-row' },
        h('div', { class: 'tp-ver-num' }, v.version_number),
        h('div', { class: 'tp-ver-main' }, h('div', {}, v.changelog || '—'), h('div', { class: 'tp-ver-when' }, (v.published_at || '').replace('T', ' ').slice(0, 19))));
      if (i < vs.length - 1) row.appendChild(h('button', { class: 'btn btn-ghost btn-sm', onClick: () => compare(vs[i + 1].id, v.id) }, `Compare to ${vs[i + 1].version_number}`));
      nodes.push(row);
    });
    mountAll(host, nodes);
  }
  async function compare(baseId, headId) {
    const [a, b] = await Promise.all([api.version(baseId), api.version(headId)]);
    const diff = snapshotDiff(a.snapshot, b.snapshot);
    const panel = h('div', { class: 'tp-section', style: { marginTop: '18px' } }, h('h3', {}, `Diff ${a.version_number} → ${b.version_number}`));
    const g = diff.events;
    if (!g.added.length && !g.removed.length && !g.changed.length) panel.appendChild(h('div', { class: 'tp-muted' }, 'No event-level differences.'));
    g.added.forEach((n) => panel.appendChild(h('div', { class: 'tp-diff-item' }, h('span', { class: 'tp-diff-mark add' }, '+'), n)));
    g.changed.forEach((n) => panel.appendChild(h('div', { class: 'tp-diff-item' }, h('span', { class: 'tp-diff-mark chg' }, '~'), n)));
    g.removed.forEach((n) => panel.appendChild(h('div', { class: 'tp-diff-item' }, h('span', { class: 'tp-diff-mark rem' }, '−'), n)));
    host.appendChild(panel); panel.scrollIntoView({ behavior: 'smooth' });
  }
  function snapshotDiff(a, b) {
    const evA = {}, evB = {};
    (a.events || []).forEach((e) => (evA[e.name] = e));
    (b.events || []).forEach((e) => (evB[e.name] = e));
    const strip = (o) => JSON.stringify(o, (k, v) => (k === 'id' ? undefined : v));
    const A = new Set(Object.keys(evA)), B = new Set(Object.keys(evB));
    return { events: {
      added: [...B].filter((n) => !A.has(n)),
      removed: [...A].filter((n) => !B.has(n)),
      changed: [...B].filter((n) => A.has(n) && strip(evA[n]) !== strip(evB[n])),
    } };
  }
  return () => {};
}
