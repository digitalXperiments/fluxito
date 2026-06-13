import test from "node:test";
import assert from "node:assert/strict";
import {
  isValidRegex,
  parseEnum,
  buildConstraints,
  usedByEvents,
} from "../../../app/static/js/tracking_plan/util/constraints.js";

test("isValidRegex accepts valid + empty, rejects malformed", () => {
  assert.equal(isValidRegex("^[a-z]+$"), true);
  assert.equal(isValidRegex(""), true);
  assert.equal(isValidRegex("   "), true);
  assert.equal(isValidRegex("[unterminated"), false);
  assert.equal(isValidRegex("("), false);
});

test("parseEnum trims, drops blanks, de-dupes", () => {
  assert.deepEqual(parseEnum("USD, EUR ,, GBP, USD"), ["USD", "EUR", "GBP"]);
  assert.deepEqual(parseEnum(""), []);
  assert.deepEqual(parseEnum(null), []);
});

test("buildConstraints assembles only present fields, else null", () => {
  assert.deepEqual(buildConstraints({ enumRaw: "", min: "", max: "", regex: "" }), null);
  assert.deepEqual(buildConstraints({ enumRaw: "A,B", min: "", max: "", regex: "" }), {
    allowed_values: ["A", "B"],
  });
  assert.deepEqual(buildConstraints({ enumRaw: "", min: "0", max: "10", regex: "\\d+" }), {
    min: 0,
    max: 10,
    regex: "\\d+",
  });
});

test("usedByEvents matches event-kind props by name; non-event kinds → []", () => {
  const plan = {
    events: [
      { id: "e1", name: "purchase", properties: [{ name: "currency" }, { name: "value" }] },
      { id: "e2", name: "signup", properties: [{ name: "method" }] },
    ],
  };
  assert.deepEqual(usedByEvents(plan, { kind: "event", name: "currency" }), [
    { id: "e1", name: "purchase" },
  ]);
  assert.deepEqual(usedByEvents(plan, { kind: "event", name: "absent" }), []);
  assert.deepEqual(usedByEvents(plan, { kind: "user", name: "currency" }), []);
});
