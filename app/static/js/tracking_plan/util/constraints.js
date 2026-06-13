// app/static/js/tracking_plan/util/constraints.js
// Pure helpers for the property editor: constraint (de)serialization, regex
// validity, and client-side "used by N events" reverse references.

/** True if `pattern` compiles as a JS RegExp. Empty/blank string is treated as
 *  "no regex" → valid. */
export function isValidRegex(pattern) {
  if (pattern == null || String(pattern).trim() === "") return true;
  try {
    new RegExp(String(pattern));
    return true;
  } catch (_e) {
    return false;
  }
}

/** "USD, EUR ,, GBP" → ["USD","EUR","GBP"] (trim, drop blanks, de-dupe). */
export function parseEnum(raw) {
  const seen = new Set();
  const out = [];
  String(raw || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .forEach((v) => {
      if (!seen.has(v)) {
        seen.add(v);
        out.push(v);
      }
    });
  return out;
}

/** Assemble the `constraints` JSONB from editor fields, or null when empty.
 *  Mirrors the service contract: allowed_values (non-empty list), min, max
 *  (numbers), regex (opaque string). `min`/`max` accept "" → omitted. */
export function buildConstraints({ enumRaw, min, max, regex }) {
  const c = {};
  const allowed = parseEnum(enumRaw);
  if (allowed.length) c.allowed_values = allowed;
  if (min !== "" && min != null && Number.isFinite(Number(min))) c.min = Number(min);
  if (max !== "" && max != null && Number.isFinite(Number(max))) c.max = Number(max);
  const rx = String(regex || "").trim();
  if (rx) c.regex = rx;
  return Object.keys(c).length ? c : null;
}

/** Events whose property list includes a library property — matched by name
 *  within the event-property kind (event.properties[] carries name, not id).
 *  Returns [{id, name}] for linking. Non-event kinds → []. */
export function usedByEvents(plan, property) {
  if (!plan || !property || property.kind !== "event") return [];
  const name = property.name;
  return (plan.events || [])
    .filter((e) => (e.properties || []).some((p) => p.name === name))
    .map((e) => ({ id: e.id, name: e.name }));
}
