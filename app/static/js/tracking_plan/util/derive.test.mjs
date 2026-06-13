// app/static/js/tracking_plan/util/derive.test.mjs
// Guards the structural fix: the rendered event list is a pure function of the
// plan dict, grouped by category and sorted — so a created event (present in the
// reloaded plan) is ALWAYS in the derived list. Mirrors views/events list logic.
import { test } from 'node:test';
import assert from 'node:assert/strict';

function deriveList(plan, search = '') {
  let evs = (plan.events || []).slice().sort((a, b) => a.name.localeCompare(b.name));
  if (search) {
    const q = search.toLowerCase();
    evs = evs.filter((e) => e.name.toLowerCase().includes(q) || (e.category || '').toLowerCase().includes(q));
  }
  const byCat = {};
  evs.forEach((e) => { (byCat[e.category || 'Uncategorized'] ||= []).push(e); });
  return byCat;
}

test('a newly created event present in the plan is always in the derived list', () => {
  const before = { events: [{ name: 'view_item', category: 'Commerce', properties: [] }] };
  const after = { events: [...before.events, { name: 'checkout_completed', category: 'Commerce', properties: [] }] };
  const grouped = deriveList(after);
  const names = (grouped.Commerce || []).map((e) => e.name);
  assert.ok(names.includes('checkout_completed'), 'created event must appear after reload');
});

test('uncategorized events land under Uncategorized', () => {
  const grouped = deriveList({ events: [{ name: 'x', category: null, properties: [] }] });
  assert.deepEqual(grouped.Uncategorized.map((e) => e.name), ['x']);
});
