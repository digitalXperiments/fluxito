"""Type-aware, per-platform filter translation.

A dashboard filter carries a *value* (e.g. ``"US"`` or ``["Organic", "Paid"]``).
The query engines need that value expressed in their own syntax:

* **GA4** — a ``dimension_filter`` ``FilterExpression`` dict (the REST/proto JSON
  shape consumed by ``app/connectors/ga4.py:_build_filter_expression``). Multiple
  filters merge into an ``andGroup``.
* **warehouse** — ``{placeholder}`` tokens in the SQL string are replaced with a
  safely-escaped fragment (quoted scalar, ``IN`` list, ``LIKE`` pattern, range).
* **marketing** — a simple call-arg param (the connectors take scalar params).
  ``search`` / ``number_range`` are not expressible and raise.

``translate`` mutates ``call_args`` in place. ``UnsupportedFilterError`` is raised
when a platform cannot express a given filter type, so deploy/update validation can
reject it instead of letting it silently no-op.
"""

from __future__ import annotations

VALID_PLATFORMS_DOC = "ga4 | warehouse | marketing connectors (meta, tiktok, ...)"

_WAREHOUSE = {"warehouse", "bigquery", "warehouse_query"}
_EMPTY = (None, "", [], {})


class UnsupportedFilterError(ValueError):
    """A filter type is not expressible for the given platform."""


def translate(platform: str, ftype: str, target: str, value: object, call_args: dict) -> None:
    """Apply one filter to ``call_args`` for ``platform``. Mutates in place.

    No-op when ``value`` is empty (an unset filter must not constrain the query).
    """
    if _is_empty(value) and ftype != "toggle":
        return
    if ftype == "toggle":
        # value is the truthiness; the override map lives in the spec and is passed
        # as `value` already resolved to the applies-dict when ON, or None when OFF.
        if not value:
            return

    p = (platform or "").lower()
    if p == "ga4":
        _translate_ga4(ftype, target, value, call_args)
    elif p in _WAREHOUSE:
        _translate_warehouse(ftype, target, value, call_args)
    else:
        _translate_marketing(p, ftype, target, value, call_args)


def _is_empty(value: object) -> bool:
    if value in _EMPTY:
        return True
    return isinstance(value, dict) and _is_empty_range(value)


def _is_empty_range(value: dict) -> bool:
    """A number_range with both bounds unset is empty."""
    if set(value.keys()) <= {"min", "max"}:
        return value.get("min") in (None, "") and value.get("max") in (None, "")
    return False


# --------------------------------------------------------------------------- GA4


def _ga4_field(target: str) -> str:
    """Field name from a hook target like ``dimension_filter.country`` -> country."""
    for prefix in ("dimension_filter.", "metric_filter."):
        if target.startswith(prefix):
            return target[len(prefix) :]
    return target


def _merge_ga4(call_args: dict, key: str, expr: dict) -> None:
    existing = call_args.get(key)
    if not existing:
        call_args[key] = expr
    elif isinstance(existing, dict) and "andGroup" in existing:
        existing["andGroup"]["expressions"].append(expr)
    else:
        call_args[key] = {"andGroup": {"expressions": [existing, expr]}}


def _ga4_string_filter(field: str, match: str, value: object) -> dict:
    return {
        "filter": {
            "fieldName": field,
            "stringFilter": {"matchType": match, "value": str(value)},
        }
    }


def _translate_ga4(ftype: str, target: str, value: object, call_args: dict) -> None:
    field = _ga4_field(target)
    if ftype == "single_select":
        _merge_ga4(call_args, "dimension_filter", _ga4_string_filter(field, "EXACT", value))
    elif ftype == "search":
        _merge_ga4(call_args, "dimension_filter", _ga4_string_filter(field, "CONTAINS", value))
    elif ftype == "multi_select":
        expr = {
            "filter": {
                "fieldName": field,
                "inListFilter": {"values": [str(v) for v in value]},  # type: ignore[union-attr]
            }
        }
        _merge_ga4(call_args, "dimension_filter", expr)
    elif ftype == "toggle":
        # value is the applies-dict, e.g. {"newVsReturning": "new"}
        for fld, val in value.items():  # type: ignore[union-attr]
            _merge_ga4(call_args, "dimension_filter", _ga4_string_filter(fld, "EXACT", val))
    elif ftype == "number_range":
        lo, hi = value.get("min"), value.get("max")  # type: ignore[union-attr]
        exprs = []
        if lo not in (None, ""):
            exprs.append(_ga4_numeric(field, "GREATER_THAN_OR_EQUAL", lo))
        if hi not in (None, ""):
            exprs.append(_ga4_numeric(field, "LESS_THAN_OR_EQUAL", hi))
        for expr in exprs:
            _merge_ga4(call_args, "metric_filter", expr)
    elif ftype == "date_range":
        return  # handled by the date pipeline, not here
    else:
        raise UnsupportedFilterError(f"GA4 cannot express filter type '{ftype}'")


def _ga4_numeric(field: str, op: str, value: object) -> dict:
    num = int(value) if float(value) == int(float(value)) else float(value)  # type: ignore[arg-type]
    val_key = "intValue" if isinstance(num, int) else "doubleValue"
    return {
        "filter": {
            "fieldName": field,
            "numericFilter": {"operation": op, "value": {val_key: num}},
        }
    }


# --------------------------------------------------------------------- warehouse


def _sql_lit(value: object) -> str:
    """Single-quoted, escaped SQL string literal (single quotes doubled)."""
    return "'" + str(value).replace("'", "''") + "'"


def _sql_num(value: object) -> str:
    """A bare numeric literal; raises if not a number (no injection surface)."""
    f = float(value)  # raises ValueError on non-numeric
    return str(int(f)) if f == int(f) else str(f)


def _sub(call_args: dict, token: str, replacement: str) -> None:
    q = call_args.get("query")
    if isinstance(q, str):
        call_args["query"] = q.replace("{" + token + "}", replacement)


def _translate_warehouse(ftype: str, target: str, value: object, call_args: dict) -> None:
    if ftype == "single_select":
        _sub(call_args, target, _sql_lit(value))
    elif ftype == "search":
        _sub(call_args, target, _sql_lit("%" + str(value) + "%"))
    elif ftype == "multi_select":
        joined = ", ".join(_sql_lit(v) for v in value)  # type: ignore[union-attr]
        _sub(call_args, target, joined)
    elif ftype == "number_range":
        lo, hi = value.get("min"), value.get("max")  # type: ignore[union-attr]
        if lo not in (None, ""):
            _sub(call_args, target + "_min", _sql_num(lo))
        if hi not in (None, ""):
            _sub(call_args, target + "_max", _sql_num(hi))
    elif ftype == "toggle":
        for fld, val in value.items():  # type: ignore[union-attr]
            _sub(call_args, fld, _sql_lit(val))
    elif ftype == "date_range":
        return
    else:
        raise UnsupportedFilterError(f"warehouse cannot express filter type '{ftype}'")


# ---------------------------------------------------------------------- marketing


def _set_path(obj: dict, path: str, value: object) -> None:
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _translate_marketing(platform: str, ftype: str, target: str, value: object, call_args: dict) -> None:
    if ftype in ("single_select", "multi_select", "date_range"):
        _set_path(call_args, target, value)
    elif ftype == "toggle":
        for fld, val in value.items():  # type: ignore[union-attr]
            _set_path(call_args, fld, val)
    else:
        raise UnsupportedFilterError(f"marketing connector '{platform}' cannot express filter type '{ftype}'")
