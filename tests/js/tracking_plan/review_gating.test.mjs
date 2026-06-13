import { test } from "node:test";
import assert from "node:assert/strict";
import { canMerge, reviewActionsFor } from "../../../app/static/js/tracking_plan/views/review.js";

test("canMerge: admin + approved only", () => {
  assert.equal(canMerge("approved", "admin"), true);
  assert.equal(canMerge("approved", "owner"), true);
  assert.equal(canMerge("approved", "editor"), false); // not admin
  assert.equal(canMerge("ready_for_review", "admin"), false); // not approved
  assert.equal(canMerge("draft", "owner"), false);
});

test("reviewActionsFor: draft → can request review", () => {
  const a = reviewActionsFor("draft", "editor", false);
  assert.deepEqual(a.map((x) => x.id), ["request_review"]);
});

test("reviewActionsFor: ready_for_review → approve + request changes (+ merge disabled for admin)", () => {
  const admin = reviewActionsFor("ready_for_review", "admin", false);
  assert.deepEqual(admin.map((x) => x.id), ["approve", "request_changes", "merge"]);
  assert.equal(admin.find((x) => x.id === "merge").disabled, true); // not approved yet
  const editor = reviewActionsFor("ready_for_review", "editor", false);
  assert.deepEqual(editor.map((x) => x.id), ["approve", "request_changes"]); // no merge for non-admin
});

test("reviewActionsFor: approved → merge enabled for admin, request changes still offered", () => {
  const admin = reviewActionsFor("approved", "admin", false);
  assert.deepEqual(admin.map((x) => x.id), ["request_changes", "merge"]);
  assert.equal(admin.find((x) => x.id === "merge").disabled, false);
});

test("reviewActionsFor: main branch → no actions", () => {
  assert.deepEqual(reviewActionsFor("draft", "admin", true), []);
});
