// app/static/js/tracking_plan/util/diff.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupDiff } from './diff.js';

const resp = {
  summary: { added: 2, removed: 1, changed: 1 },
  events: {
    added: [{ id: 'e1', name: 'signup' }],
    removed: [{ id: 'e2', name: 'logout' }],
    changed: [{ id: 'e3', name: 'purchase', fields: { purpose: { was: 'a', now: 'b' } } }],
  },
  properties: {
    event: { added: [{ id: 'p1', name: 'currency' }], removed: [], changed: [] },
    user: { added: [], removed: [], changed: [] },
    group: { added: [], removed: [], changed: [] },
    system: { added: [], removed: [], changed: [] },
  },
  sources: { added: [], removed: [], changed: [] },
  destinations: { added: [], removed: [], changed: [] },
  metrics: { added: [], removed: [], changed: [] },
  categories: { added: [], removed: [], changed: [] },
};

test('groupDiff returns one entry per non-empty group, in canonical order', () => {
  const groups = groupDiff(resp);
  assert.deepEqual(groups.map((g) => g.group), ['Events', 'Properties']);
});

test('groupDiff yields markers +/~/- per change', () => {
  const ev = groupDiff(resp).find((g) => g.group === 'Events');
  const byName = Object.fromEntries(ev.changes.map((c) => [c.name, c.marker]));
  assert.equal(byName.signup, '+');
  assert.equal(byName.logout, '-');
  assert.equal(byName.purchase, '~');
});

test('groupDiff carries entityType, id, and field-level was/now for changes', () => {
  const ev = groupDiff(resp).find((g) => g.group === 'Events');
  const purchase = ev.changes.find((c) => c.name === 'purchase');
  assert.equal(purchase.entityType, 'event');
  assert.equal(purchase.id, 'e3');
  assert.deepEqual(purchase.fields, [{ key: 'purpose', was: 'a', now: 'b' }]);
});

test('groupDiff flattens properties across kinds under one Properties group', () => {
  const props = groupDiff(resp).find((g) => g.group === 'Properties');
  assert.equal(props.changes.length, 1);
  assert.equal(props.changes[0].name, 'currency');
  assert.equal(props.changes[0].entityType, 'property');
});

test('groupDiff returns [] for an all-empty diff', () => {
  const empty = JSON.parse(JSON.stringify(resp));
  for (const k of ['events', 'sources', 'destinations', 'metrics', 'categories']) {
    empty[k] = { added: [], removed: [], changed: [] };
  }
  empty.properties = {
    event: { added: [], removed: [], changed: [] },
    user: { added: [], removed: [], changed: [] },
    group: { added: [], removed: [], changed: [] },
    system: { added: [], removed: [], changed: [] },
  };
  assert.deepEqual(groupDiff(empty), []);
});
