// app/static/js/tracking_plan/util/format.js
// Pure presentation + lookup helpers. The lookups resolve the name-keyed plan
// dict to library UUIDs (see contract clarification #1): events[].properties and
// events[].sources carry NAMES; write actions need IDs.

export function initials(s) {
  return (s || '?').replace(/[^a-z0-9]/gi, '').slice(0, 2).toUpperCase();
}

export function typeBadge(dataType, isList) {
  // data_type is one of: string, integer, float, boolean, object.
  // is_list is the orthogonal "list of X" modifier — renders as X[].
  return (dataType || 'string') + (isList ? '[]' : '');
}

export function relativeTime(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return m + 'm ago';
  const hrs = Math.floor(m / 60);
  if (hrs < 24) return hrs + 'h ago';
  const d = Math.floor(hrs / 24);
  if (d < 30) return d + 'd ago';
  return new Date(iso).toLocaleDateString();
}

export function titleCase(s) {
  return (s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// ---- name → library lookups (read from getState().plan) -------------------

export function allProps(plan) {
  const p = plan.properties || { event: [], user: [], group: [], system: [] };
  return [...p.event, ...p.user, ...p.group, ...p.system];
}
export function propByName(plan, name) { return allProps(plan).find((x) => x.name === name) || null; }
export function eventProps(plan) { return (plan.properties && plan.properties.event) || []; }
export function sourceByName(plan, name) { return (plan.sources || []).find((s) => s.name === name) || null; }
export function destByName(plan, name) { return (plan.destinations || []).find((d) => d.name === name) || null; }
export function eventByName(plan, name) { return (plan.events || []).find((e) => e.name === name) || null; }
export function catByName(plan, name) {
  const c = (plan.categories || []).find((x) => x.name === name);
  return c ? c.id : null;
}

// Per-event derived status dot: green=all verified, amber=any implemented,
// grey=planned/none. Mirrors the old per-row dot intent (spec §5.2).
export function eventStatus(ev) {
  const ss = (ev.sources || []).map((s) => s.implementation_status);
  if (ss.length && ss.every((s) => s === 'verified')) return 'verified';
  if (ss.some((s) => s === 'implemented' || s === 'verified')) return 'implemented';
  return 'planned';
}
