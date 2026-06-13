import { test } from "node:test";
import assert from "node:assert/strict";
import { groupDiff, fieldDiff } from "../../../app/static/js/tracking_plan/util/diff.js";

test("groupDiff derives field-level was/now for changed entities", () => {
  const resp = {
    events: {
      added: [], removed: [],
      changed: [{ name: "purchase",
        before: { id: "a", name: "purchase", purpose: "old", required: false },
        after:  { id: "b", name: "purchase", purpose: "new", required: true } }],
    },
    properties: { event: {added:[],removed:[],changed:[]}, user:{added:[],removed:[],changed:[]},
                  group:{added:[],removed:[],changed:[]}, system:{added:[],removed:[],changed:[]} },
    sources: {added:[],removed:[],changed:[]}, destinations:{added:[],removed:[],changed:[]},
    metrics:{added:[],removed:[],changed:[]}, categories:{added:[],removed:[],changed:[]},
    summary: { added: 0, removed: 0, changed: 1 },
  };
  const groups = groupDiff(resp);
  const evGroup = groups.find((g) => g.group === "Events");
  const change = evGroup.changes[0];
  assert.equal(change.marker, "~");
  const keys = change.fields.map((f) => f.key).sort();
  assert.deepEqual(keys, ["purpose", "required"]); // id excluded as volatile
  const purpose = change.fields.find((f) => f.key === "purpose");
  assert.deepEqual([purpose.was, purpose.now], ["old", "new"]);
});

test("fieldDiff skips volatile keys", () => {
  const before = { id: "1", branch_id: "b", name: "foo", description: "old" };
  const after  = { id: "2", branch_id: "c", name: "foo", description: "new" };
  const fields = fieldDiff(before, after);
  assert.deepEqual(fields.map((f) => f.key), ["description"]);
});

test("fieldDiff handles null/undefined values", () => {
  const fields = fieldDiff({ a: null }, { a: "x" });
  assert.deepEqual(fields, [{ key: "a", was: "", now: "x" }]);
});

test("fieldDiff stringifies objects", () => {
  const fields = fieldDiff({ props: { x: 1 } }, { props: { x: 2 } });
  assert.equal(fields[0].key, "props");
  assert.equal(fields[0].was, '{"x":1}');
  assert.equal(fields[0].now, '{"x":2}');
});

test("groupDiff: added and removed entries have empty fields", () => {
  const resp = {
    events: {
      added: [{ id: "1", name: "new_event" }],
      removed: [{ id: "2", name: "old_event" }],
      changed: [],
    },
    properties: { event:{added:[],removed:[],changed:[]}, user:{added:[],removed:[],changed:[]},
                  group:{added:[],removed:[],changed:[]}, system:{added:[],removed:[],changed:[]} },
    sources:{added:[],removed:[],changed:[]}, destinations:{added:[],removed:[],changed:[]},
    metrics:{added:[],removed:[],changed:[]}, categories:{added:[],removed:[],changed:[]},
    summary: { added: 1, removed: 1, changed: 0 },
  };
  const groups = groupDiff(resp);
  const evGroup = groups.find((g) => g.group === "Events");
  const added = evGroup.changes.find((c) => c.marker === "+");
  const removed = evGroup.changes.find((c) => c.marker === "-");
  assert.deepEqual(added.fields, []);
  assert.deepEqual(removed.fields, []);
});
