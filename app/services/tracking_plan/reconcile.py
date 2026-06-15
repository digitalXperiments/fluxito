# app/services/tracking_plan/reconcile.py
"""Pure-function helpers for reconcile_preview / reconcile_apply.

All functions are deterministic, side-effect-free, and require no DB access
so they are independently unit-testable. The tool layer (tracking_plan_tools.py)
calls them after fetching plan_to_dict and passes the result into diff_events.

Casing support
--------------
normalize_name handles four strategies:

    snake_case  — "Add To Cart" → "add_to_cart"   (default, plan standard)
    camelCase   — "Add To Cart" → "addToCart"
    Title       — "Add To Cart" → "Add To Cart"    (title-joined)
    none        — raw.strip(), no change

Tokenization splits on: spaces, hyphens, underscores, and camelCase boundaries
(uppercase letter preceded by a lowercase/digit), producing a clean word list
that all three non-none casings share.

match_key
---------
Canonical dedup key for fuzzy matching: lowercase + collapse all [ _\\-\\s]→"".
"Add To Cart", "add_to_cart", "addToCart" all resolve to "addtocart".
Exact match strategy: use the literal name instead of match_key.

diff_events
-----------
Deterministic diff of an incoming normalized event list against the current
plan_to_dict["events"]. Returns:
    new        – events absent from the plan
    updated    – events present with field-level changes
    unchanged  – events present with no changes
    conflicts  – duplicate input keys or casing-mismatch warnings
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Casing helpers
# ---------------------------------------------------------------------------


def _tokenize(raw: str) -> list[str]:
    """Split a raw name into lowercase word tokens.

    Handles: spaces, hyphens, underscores, and camelCase boundaries.
    Empty tokens are filtered out.
    """
    # Insert spaces before uppercase letters that follow lowercase/digit
    # (camelCase / PascalCase boundary), then split on any separator.
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw.strip())
    # Also split on runs of uppercase followed by a capitalized word
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    tokens = re.split(r"[ _\-]+", s)
    return [t.lower() for t in tokens if t]


def normalize_name(raw: str, casing: str = "snake_case") -> str:
    """Normalize *raw* to the requested casing.

    casing ∈ {"snake_case", "camelCase", "Title", "none"}.
    Empty/whitespace input always returns "".
    """
    if not raw or not raw.strip():
        return ""
    if casing == "none":
        return raw.strip()
    tokens = _tokenize(raw)
    if not tokens:
        return ""
    if casing == "snake_case":
        return "_".join(tokens)
    if casing == "camelCase":
        return tokens[0] + "".join(t.capitalize() for t in tokens[1:])
    if casing in ("Title", "TitleCase"):
        return " ".join(t.capitalize() for t in tokens)
    # Unknown casing — fall back to identity
    return raw.strip()


# ---------------------------------------------------------------------------
# Fuzzy-match key
# ---------------------------------------------------------------------------


def match_key(name: str) -> str:
    """Canonical dedup key: lowercase, strip, collapse all separators to "".

    "Add To Cart", "add_to_cart", "addToCart" → "addtocart".
    """
    collapsed = re.sub(r"[ _\-]+", "", name.lower().strip())
    return collapsed


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------


def diff_events(
    incoming: list[dict],
    current_events: list[dict],
    *,
    match_strategy: str = "fuzzy",
) -> dict:
    """Diff *incoming* (normalized) against *current_events* (from plan_to_dict).

    Parameters
    ----------
    incoming:
        List of event dicts, each with at minimum {"name": str}.
        Optional keys: display_name, description, category, trigger, source,
        properties (list of dicts with at minimum {"name": str}).
    current_events:
        The ``events`` list from plan_to_dict — each item carries
        {"id": str, "name": str, "category": str|None, ...}.
    match_strategy:
        "fuzzy"  — match_key used for dedup ("add_to_cart" == "addToCart")
        "exact"  — literal name comparison

    Returns
    -------
    dict with keys: new, updated, unchanged, conflicts
        new       list[dict]  – {name, display_name?, description?, category?,
                                  trigger?, source?, properties?}
        updated   list[dict]  – {name, event_id, changes{...}}
        unchanged list[dict]  – {name}
        conflicts list[dict]  – {reason, ...}

    The lists are sorted by "name" (stable). No randomness or time is used
    — repeated calls with the same inputs return byte-identical results.
    """

    def _key(name: str) -> str:
        return match_key(name) if match_strategy == "fuzzy" else name

    # Build current index
    current_by_key: dict[str, dict] = {}
    for ev in current_events:
        current_by_key[_key(ev["name"])] = ev

    # Build current property-name sets per event key (for property delta)
    current_props_by_key: dict[str, set[str]] = {}
    for ev in current_events:
        current_props_by_key[_key(ev["name"])] = {p["name"] for p in (ev.get("properties") or [])}

    # Dedup incoming by key — first occurrence wins
    seen_input_keys: dict[str, str] = {}  # key -> first input name
    dup_groups: dict[str, list[str]] = {}  # key -> all names (for conflict)
    for ev in incoming:
        k = _key(ev.get("name") or "")
        name = ev.get("name") or ""
        if k not in seen_input_keys:
            seen_input_keys[k] = name
        else:
            dup_groups.setdefault(k, [seen_input_keys[k]]).append(name)

    # Process each unique incoming event (first occurrence only)
    processed_keys: set[str] = set()
    new_list: list[dict] = []
    updated_list: list[dict] = []
    unchanged_list: list[dict] = []
    conflicts: list[dict] = []

    # Report duplicate-input conflicts first
    for k, names in dup_groups.items():
        conflicts.append({"reason": "duplicate_input", "names": names})

    for ev in incoming:
        name = ev.get("name") or ""
        k = _key(name)
        if k in processed_keys:
            continue
        processed_keys.add(k)

        if k not in current_by_key:
            # New event — carry all incoming fields
            entry: dict = {"name": name}
            for field in ("display_name", "description", "category", "trigger", "source"):
                if ev.get(field) is not None:
                    entry[field] = ev[field]
            if ev.get("properties"):
                entry["properties"] = ev["properties"]
            new_list.append(entry)
        else:
            current = current_by_key[k]
            current_name = current["name"]

            # Scalar field delta: only include keys whose incoming value is
            # non-None AND differs from the current value.
            changes: dict = {}
            for field in ("display_name", "description", "category"):
                incoming_val = ev.get(field)
                if incoming_val is None:
                    continue
                current_val = current.get(field)
                if incoming_val != current_val:
                    changes[field] = {"from": current_val, "to": incoming_val}

            # Property delta — incoming property names not yet on the event
            current_props = current_props_by_key.get(k, set())
            incoming_prop_names = [p.get("name") or "" for p in (ev.get("properties") or []) if p.get("name")]
            props_to_add = [
                p for p in (ev.get("properties") or []) if (p.get("name") or "") not in current_props
            ]
            if props_to_add:
                changes["properties_to_add"] = props_to_add

            # Casing mismatch sub-case: different names that share a fuzzy key.
            # Surface as a conflict note but still process as an update
            # (the caller decides via decisions).
            if match_strategy == "fuzzy" and name != current_name:
                conflicts.append(
                    {
                        "reason": "casing_mismatch",
                        "input": name,
                        "matched": current_name,
                        "suggested": name,
                    }
                )

            if changes:
                updated_list.append(
                    {
                        "name": current_name,
                        "event_id": current["id"],
                        "changes": changes,
                    }
                )
            else:
                unchanged_list.append({"name": current_name})

    # Stable sort by name for determinism
    new_list.sort(key=lambda x: x["name"])
    updated_list.sort(key=lambda x: x["name"])
    unchanged_list.sort(key=lambda x: x["name"])
    conflicts.sort(key=lambda x: x.get("names", [x.get("input", "")])[0])

    return {
        "new": new_list,
        "updated": updated_list,
        "unchanged": unchanged_list,
        "conflicts": conflicts,
    }
