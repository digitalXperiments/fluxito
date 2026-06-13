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

function _fields(item) {
  // changed items may carry fields as { key: {was, now} }.
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
