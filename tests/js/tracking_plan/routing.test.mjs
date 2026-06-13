import test from "node:test";
import assert from "node:assert/strict";
import { routingLinks, isLinked } from "../../../app/static/js/tracking_plan/util/routing.js";

const plan = {
  destinations: [
    { id: "d1", name: "GA4" },
    { id: "d2", name: "Amplitude" },
  ],
  sources: [
    { id: "s1", name: "Web", destinations: ["GA4", "Amplitude"] },
    { id: "s2", name: "iOS", destinations: ["GA4"] },
    { id: "s3", name: "Server", destinations: ["Stale"] }, // unresolved → dropped
  ],
};

test("routingLinks resolves names→ids and drops unresolved", () => {
  assert.deepEqual(routingLinks(plan), [
    { sourceId: "s1", destinationId: "d1" },
    { sourceId: "s1", destinationId: "d2" },
    { sourceId: "s2", destinationId: "d1" },
  ]);
});

test("isLinked predicate", () => {
  assert.equal(isLinked(plan, "s1", "d2"), true);
  assert.equal(isLinked(plan, "s2", "d2"), false);
  assert.equal(isLinked(plan, "s3", "d1"), false);
});
