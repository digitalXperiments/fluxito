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

// Real, always-visible one-line human description for a change row (design:
// TP Versions compare — every added/changed/removed row shows a plain-language
// summary next to the name, never hidden behind a click). Derived purely from
// the actual diff payload (full entity for +/-, field diff for ~) — no
// fabricated narrative (the design's "no traffic since Feb" style flavor text
// isn't backed by real data, so we don't invent it).
function describeEntity(entityType, item) {
  if (!item) return '';
  if (entityType === 'event') {
    const n = (item.properties || []).length;
    const dests = (item.destinations || []).map((d) => d.destination).filter(Boolean);
    const bits = [`${n} param${n === 1 ? '' : 's'}`];
    if (dests.length) bits.push(dests.join(' + '));
    return bits.join(' · ');
  }
  if (entityType === 'property') {
    const dt = (item.data_type || 'string') + (item.is_list ? '[]' : '');
    const c = item.constraints || {};
    if (c.allowed_values && c.allowed_values.length) return `enum: ${c.allowed_values.join(' | ')}`;
    return dt + (item.is_pii ? ' · PII' : '');
  }
  if (entityType === 'destination' || entityType === 'source') {
    return item.category || item.platform || item.slug || '';
  }
  if (entityType === 'metric') {
    return item.description || (item.event_name ? `on ${item.event_name}` : '');
  }
  if (entityType === 'category') {
    return item.color ? `color ${item.color}` : '';
  }
  return '';
}

function describeFieldChanges(fields) {
  if (!fields || !fields.length) return '';
  const nameField = fields.find((f) => f.key === 'name');
  const rest = fields.filter((f) => f.key !== 'name');
  const parts = [];
  if (nameField) parts.push(`renamed ${nameField.was} → ${nameField.now}`);
  if (rest.length) {
    const keys = rest.slice(0, 2).map((f) => f.key).join(', ');
    parts.push(`${keys}${rest.length > 2 ? ` +${rest.length - 2} more` : ''} changed`);
  }
  return parts.join(' · ');
}

function _changes(bucket, entityType) {
  const out = [];
  for (const [list, marker] of [['added', '+'], ['changed', '~'], ['removed', '-']]) {
    for (const item of bucket[list] || []) {
      const fields = marker === '~' ? _fields(item) : [];
      // added/removed items ARE the full entity dict; changed items carry it
      // under .after (fall back to the raw item if absent).
      const entity = marker === '~' ? (item.after || item) : item;
      out.push({
        marker,
        entityType,
        name: item.name,
        id: item.id != null ? String(item.id) : null,
        fields,
        description: marker === '~' ? describeFieldChanges(fields) : describeEntity(entityType, entity),
      });
    }
  }
  return out;
}

// Stable per-change key used to key review decisions (state.js) and comment/
// activity badges (_changelist.js). entityType + id when present, else name
// (added items sometimes lack a persisted id in the diff payload).
export function changeKey(c) {
  return `${c.entityType}:${c.id || c.name}`;
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
