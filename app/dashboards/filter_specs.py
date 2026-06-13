"""Validation + normalization for dashboard-level filter specs.

A dashboard's ``filters`` column is a list of filter-widget declarations. This
module is the single gate that validates and normalizes them into a canonical
shape consumed by the UI (``filter_bar.html``), the data path
(``filter_translators``), and the MCP deploy/update tools.

Six widget types are supported:
  date_range | single_select | multi_select | search | number_range | toggle
"""

from __future__ import annotations

VALID_TYPES = {
    "date_range",
    "single_select",
    "multi_select",
    "search",
    "number_range",
    "toggle",
}

MAX_FILTERS = 20


class FilterSpecError(ValueError):
    """Raised when a dashboard filter spec is malformed."""


def validate_filters(filters: list[dict] | None) -> list[dict]:
    """Validate + normalize a list of filter specs.

    Returns the normalized list (with defaults filled in). Raises
    ``FilterSpecError`` on the first problem so deploy/update can surface a
    clear, actionable message instead of silently dropping filters.
    """
    if not filters:
        return []
    if not isinstance(filters, list):
        raise FilterSpecError("filters must be a list")
    if len(filters) > MAX_FILTERS:
        raise FilterSpecError(f"at most {MAX_FILTERS} filters per dashboard")

    seen: set[str] = set()
    out: list[dict] = []
    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            raise FilterSpecError(f"filter[{i}] must be an object")

        key = f.get("key")
        if not key or not isinstance(key, str):
            raise FilterSpecError(f"filter[{i}] missing string 'key'")
        if key in seen:
            raise FilterSpecError(f"duplicate filter key '{key}'")
        seen.add(key)

        ftype = f.get("type")
        if ftype not in VALID_TYPES:
            raise FilterSpecError(f"filter '{key}' has invalid type '{ftype}'")

        spec: dict = {
            "key": key,
            "label": f.get("label") or key,
            "type": ftype,
            "ui": f.get("ui") or {},
        }

        if ftype in ("single_select", "multi_select"):
            opts = f.get("options") or {}
            src = opts.get("source")
            if src not in ("static", "warehouse"):
                raise FilterSpecError(f"filter '{key}' options.source must be 'static' or 'warehouse'")
            if src == "static" and not isinstance(opts.get("values"), list):
                raise FilterSpecError(f"filter '{key}' static options need a 'values' list")
            if src == "warehouse" and not (opts.get("card") and opts.get("column")):
                raise FilterSpecError(f"filter '{key}' warehouse options need 'card' and 'column'")
            spec["options"] = opts

        if ftype == "toggle":
            applies = (f.get("toggle") or {}).get("applies")
            if not isinstance(applies, dict) or not applies:
                raise FilterSpecError(f"toggle '{key}' needs a non-empty toggle.applies map")
            spec["toggle"] = {"applies": applies}

        spec["default"] = _default_for(ftype, f.get("default"))
        out.append(spec)

    return out


def _default_for(ftype: str, given: object) -> object:
    """Return a type-appropriate default value."""
    if ftype == "multi_select":
        return given if isinstance(given, list) else []
    if ftype == "number_range":
        g = given if isinstance(given, dict) else {}
        return {"min": g.get("min"), "max": g.get("max")}
    if ftype == "toggle":
        return bool(given)
    if ftype == "date_range":
        return given if isinstance(given, dict) else {}
    # single_select | search
    return given if given is not None else ""
