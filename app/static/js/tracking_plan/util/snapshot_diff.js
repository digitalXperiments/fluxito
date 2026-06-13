// app/static/js/tracking_plan/util/snapshot_diff.js
// Pure, browser+node. Derive a diff-RESPONSE-shaped object from two plan
// snapshots (api.version(id).snapshot). Output matches the /tracking-plan/diff
// payload so it feeds the SAME tp/util/diff.groupDiff renderer.

const VOLATILE = new Set(["id", "branch_id", "plan_id", "created_at", "updated_at", "sort_order"]);

function strip(value) {
  if (Array.isArray(value)) return value.map(strip);
  if (value && typeof value === "object") {
    const out = {};
    for (const k of Object.keys(value)) if (!VOLATILE.has(k)) out[k] = strip(value[k]);
    return out;
  }
  return value;
}
const normalized = (v) => JSON.stringify(strip(v));

export function diffCollection(baseItems = [], headItems = [], key = "name") {
  const baseBy = new Map(baseItems.map((i) => [i[key], i]));
  const headBy = new Map(headItems.map((i) => [i[key], i]));
  const added = [...headBy.keys()].filter((k) => !baseBy.has(k)).map((k) => headBy.get(k));
  const removed = [...baseBy.keys()].filter((k) => !headBy.has(k)).map((k) => baseBy.get(k));
  const changed = [];
  for (const k of headBy.keys()) {
    if (baseBy.has(k) && normalized(baseBy.get(k)) !== normalized(headBy.get(k))) {
      changed.push({ name: k, before: baseBy.get(k), after: headBy.get(k) });
    }
  }
  return { added, removed, changed };
}

const KINDS = ["event", "user", "group", "system"];

export function diffSnapshots(base = {}, head = {}) {
  const flat = (k) => diffCollection(base[k] || [], head[k] || []);
  const events = flat("events");
  const categories = flat("categories");
  const sources = flat("sources");
  const destinations = flat("destinations");
  const metrics = flat("metrics");

  const bp = base.properties || {};
  const hp = head.properties || {};
  const properties = {};
  for (const kind of KINDS) properties[kind] = diffCollection(bp[kind] || [], hp[kind] || []);

  const count = (c, field) => c[field].length;
  let added = 0, removed = 0, changed = 0;
  for (const c of [events, categories, sources, destinations, metrics]) {
    added += count(c, "added"); removed += count(c, "removed"); changed += count(c, "changed");
  }
  for (const kind of KINDS) {
    const c = properties[kind];
    added += count(c, "added"); removed += count(c, "removed"); changed += count(c, "changed");
  }

  return {
    events, properties, sources, destinations, metrics, categories,
    summary: { added, removed, changed },
  };
}
