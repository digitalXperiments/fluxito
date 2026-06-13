// app/static/js/tracking_plan/views/review.js
import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { groupDiff } from 'tp/util/diff';

export function mountView(container) {
  const host = h('div', { class: 'tp-detail-inner' });
  mountAll(container, [host]);
  load();
  async function load() {
    mountAll(host, [h('div', { class: 'tp-muted' }, 'Loading changes…')]);
    let diff;
    try { diff = await api.diff(state.getState().branch, 'main'); }
    catch (e) { mountAll(host, [h('div', { class: 'tp-empty' }, String(e.message || e))]); return; }
    const s = diff.summary || { added: 0, changed: 0, removed: 0 };
    const groups = groupDiff(diff);
    const nodes = [
      h('h2', { style: { margin: '0 0 12px' } }, 'Changes vs ', h('span', { class: 'tp-mono' }, 'main')),
      h('div', { class: 'tp-diff-summary' },
        h('span', { class: 'tp-diff-stat add' }, `+${s.added} added`),
        h('span', { class: 'tp-diff-stat chg' }, `~${s.changed} changed`),
        h('span', { class: 'tp-diff-stat rem' }, `−${s.removed} removed`)),
    ];
    if (!groups.length) nodes.push(h('div', { class: 'tp-empty' }, 'No differences from main yet.'));
    for (const g of groups) {
      const grp = h('div', { class: 'tp-diff-group' }, h('h3', {}, g.group));
      for (const c of g.changes) {
        const markCls = c.marker === '+' ? 'add' : c.marker === '-' ? 'rem' : 'chg';
        const item = h('div', { class: 'tp-diff-item' }, h('span', { class: 'tp-diff-mark ' + markCls }, c.marker), c.name);
        grp.appendChild(item);
        for (const f of c.fields || []) grp.appendChild(h('div', { class: 'tp-diff-field' }, `${f.key}: `, h('span', { class: 'tp-was' }, String(f.was ?? '∅')), ' → ', h('span', { class: 'tp-now' }, String(f.now ?? '∅'))));
      }
      nodes.push(grp);
    }
    mountAll(host, nodes);
  }
  return () => {};
}
