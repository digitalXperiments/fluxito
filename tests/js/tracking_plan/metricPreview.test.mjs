import test from "node:test";
import assert from "node:assert/strict";
import { metricPreview } from "../../../app/static/js/tracking_plan/util/metricPreview.js";

test("count / unique / sum / average phrasing", () => {
  assert.equal(metricPreview({ type: "count", event: "purchase" }), "Count of purchase");
  assert.equal(
    metricPreview({ type: "unique", event: "purchase" }),
    "Unique events of purchase"
  );
  assert.equal(
    metricPreview({ type: "sum", event: "purchase", property: "value" }),
    "Sum of value over purchase"
  );
  assert.equal(
    metricPreview({ type: "average", event: "purchase", property: "value" }),
    "Average value per purchase"
  );
});

test("filters append a where-clause", () => {
  assert.equal(
    metricPreview({ type: "unique", event: "purchase", filters: { currency: "USD" } }),
    "Unique events of purchase where currency = USD"
  );
  assert.equal(
    metricPreview({ type: "count", event: "p", filters: { a: "1", b: "2" } }),
    "Count of p where a = 1 and b = 2"
  );
});

test("empty metric → guidance string", () => {
  assert.equal(metricPreview({}), "Define a metric type to preview.");
  assert.equal(metricPreview(null), "Define a metric type to preview.");
});
