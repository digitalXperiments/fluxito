// app/static/js/tracking_plan/util/metricPreview.js
// Pure human-readable preview for a metric definition.
// Example: { type:"unique", event:"purchase", property:null,
//            filters:{currency:"USD"} } → "Unique events of purchase where currency = USD".

const VERB = {
  count: (ev) => `Count of ${ev}`,
  sum: (ev, prop) => `Sum of ${prop} over ${ev}`,
  unique: (ev) => `Unique events of ${ev}`,
  average: (ev, prop) => `Average ${prop} per ${ev}`,
  ratio: (ev) => `Ratio over ${ev}`,
};

/** Build the preview sentence. `metric` = {type, event, property, filters}.
 *  event/property are *names* (as the serializer emits) or null. */
export function metricPreview(metric) {
  if (!metric || !metric.type) return "Define a metric type to preview.";
  const ev = metric.event || "—";
  const prop = metric.property || "—";
  const head = (VERB[metric.type] || (() => `${metric.type} over ${ev}`))(ev, prop);
  const f = metric.filters || {};
  const parts = Object.keys(f)
    .filter((k) => k !== "")
    .map((k) => `${k} = ${f[k]}`);
  return parts.length ? `${head} where ${parts.join(" and ")}` : head;
}
