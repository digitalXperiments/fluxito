// app/static/js/tracking_plan/util/routing.js
// Pure derivation of the source→destination routing graph from the plan dict.
// The serializer stores each source's links as destination *names*; we resolve
// them back to ids here so the view can connect/disconnect by id.

/** Map of destination name → id from the plan. */
function destNameToId(plan) {
  const m = new Map();
  (plan.destinations || []).forEach((d) => m.set(d.name, d.id));
  return m;
}

/** All routing links as [{sourceId, destinationId}], dropping any name that no
 *  longer resolves to a destination (defensive against stale data). */
export function routingLinks(plan) {
  const byName = destNameToId(plan);
  const out = [];
  (plan.sources || []).forEach((s) => {
    (s.destinations || []).forEach((dn) => {
      const did = byName.get(dn);
      if (did) out.push({ sourceId: s.id, destinationId: did });
    });
  });
  return out;
}

/** True if source→destination is already routed. */
export function isLinked(plan, sourceId, destinationId) {
  return routingLinks(plan).some(
    (l) => l.sourceId === sourceId && l.destinationId === destinationId
  );
}
