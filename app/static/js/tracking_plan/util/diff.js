// app/static/js/tracking_plan/util/diff.js
// Pure: normalize the /diff endpoint response into a grouped, marker-tagged list
// for the review screen (and version compare). Properties (nested by kind) are
// flattened into one Properties group.

const GROUPS = [
  ['events', 'Events', 'event'],
  ['properties', 'Properties', 'property'],
  ['sources', 'Sources', 'source'],
  ['destinations', 'Destinations', 'destination'],
  ['metrics', 'Metrics', 'metric'],
  ['categories', 'Categories', 'category'],
];

const VOLATILE = new Set(['id', 'branch_id', 'plan_id', 'created_at', 'updated_at', 'sort_order']);

function scalarish(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function fieldDiff(before, after) {
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  const out = [];
  for (const key of keys) {
    if (VOLATILE.has(key)) continue;
    const was = scalarish((before || {})[key]);
    const now = scalarish((after || {})[key]);
    if (was !== now) out.push({ key, was, now });
  }
  return out.sort((a, b) => a.key.localeCompare(b.key));
}

function _fields(item) {
  // Derive field-level diff from before/after (diff endpoint shape).
  if (item.before !== undefined || item.after !== undefined) {
    return fieldDiff(item.before, item.after);
  }
  // Legacy fallback: changed items may carry fields as { key: {was, now} }.
  const f = item.fields;
  if (!f || typeof f !== 'object') return [];
  return Object.entries(f).map(([key, v]) => ({
    key,
    was: v && typeof v === 'object' ? v.was : undefined,
    now: v && typeof v === 'object' ? v.now : undefined,
  }));
}

function _changes(bucket, entityType) {
  const out = [];
  for (const [list, marker] of [['added', '+'], ['changed', '~'], ['removed', '-']]) {
    for (const item of bucket[list] || []) {
      out.push({
        marker,
        entityType,
        name: item.name,
        id: item.id != null ? String(item.id) : null,
        fields: marker === '~' ? _fields(item) : [],
      });
    }
  }
  return out;
}

export function groupDiff(diffResp) {
  if (!diffResp) return [];
  const result = [];
  for (const [key, label, entityType] of GROUPS) {
    const g = diffResp[key];
    if (!g) continue;
    let changes = [];
    if (key === 'properties') {
      for (const kind of ['event', 'user', 'group', 'system']) {
        if (g[kind]) changes = changes.concat(_changes(g[kind], 'property'));
      }
    } else {
      changes = _changes(g, entityType);
    }
    if (changes.length) result.push({ group: label, changes });
  }
  return result;
}
