import { test } from "node:test";
import assert from "node:assert/strict";
import { diffSnapshots } from "../../../app/static/js/tracking_plan/util/snapshot_diff.js";

const snap = (over = {}) => ({
  events: [], categories: [], sources: [], destinations: [], metrics: [],
  properties: { event: [], user: [], group: [], system: [] },
  ...over,
});

test("diffSnapshots: added / removed / changed across flat collections", () => {
  const base = snap({ events: [{ id: "1", name: "view", purpose: "a" }, { id: "2", name: "gone", purpose: "x" }] });
  const head = snap({ events: [{ id: "9", name: "view", purpose: "b" }, { id: "3", name: "new", purpose: "y" }] });
  const d = diffSnapshots(base, head);
  assert.deepEqual(d.events.added.map((e) => e.name), ["new"]);
  assert.deepEqual(d.events.removed.map((e) => e.name), ["gone"]);
  assert.deepEqual(d.events.changed.map((c) => c.name), ["view"]);
  // changed carries before/after so groupDiff can derive fields (matches the endpoint shape)
  assert.equal(d.events.changed[0].before.purpose, "a");
  assert.equal(d.events.changed[0].after.purpose, "b");
});

test("diffSnapshots: ids are ignored when deciding 'changed'", () => {
  const base = snap({ sources: [{ id: "aaa", name: "web", platform_type: "web" }] });
  const head = snap({ sources: [{ id: "zzz", name: "web", platform_type: "web" }] });
  assert.deepEqual(diffSnapshots(base, head).sources.changed, []); // only id differs → not changed
});

test("diffSnapshots: properties diff per kind and summary counts", () => {
  const base = snap({ properties: { event: [{ id: "1", name: "p", data_type: "string" }], user: [], group: [], system: [] } });
  const head = snap({ properties: { event: [{ id: "1", name: "p", data_type: "int" }], user: [{ id: "2", name: "u" }], group: [], system: [] } });
  const d = diffSnapshots(base, head);
  assert.deepEqual(d.properties.event.changed.map((c) => c.name), ["p"]);
  assert.deepEqual(d.properties.user.added.map((p) => p.name), ["u"]);
  assert.deepEqual(d.summary, { added: 1, removed: 0, changed: 1 });
});

test("diffSnapshots: tolerates missing collections", () => {
  const d = diffSnapshots({}, {});
  assert.deepEqual(d.summary, { added: 0, removed: 0, changed: 0 });
  assert.deepEqual(d.events, { added: [], removed: [], changed: [] });
});
