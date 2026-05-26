"""Apply user-filter overrides to a card's stored tool-call params.

Each card's ``query_params`` JSONB can include a ``filter_hooks`` map:

    {
      "filter_hooks": {
        "date_range.start": "params.start_date",
        "date_range.end":   "params.end_date",
        "country":          "params.filters.country"
      }
    }

Keys on the left are dot-path names the browser sends in ``overrides``
(nested paths are flattened automatically, so the request can send
``{"date_range": {"start": "..."}}`` and it resolves to ``date_range.start``).

Values on the right are dot-paths into the spec dict where the value
should be written. Supported path syntax: ``foo.bar``, ``foo[0]``,
``foo[0].bar``.

Applying overrides is deep-copy-safe — the caller's spec is not mutated.
"""

from __future__ import annotations

import copy
import re
from typing import Any

_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _tokenize(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for m in _TOKEN_RE.finditer(path):
        key, idx = m.groups()
        if key is not None:
            tokens.append(key)
        else:
            tokens.append(int(idx))
    return tokens


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Turn ``{"date_range": {"start": "...", "end": "..."}}`` into
    ``{"date_range.start": "...", "date_range.end": "..."}``.

    Lists and scalars are left as-is under the prefix key.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(_flatten(v, key))
            else:
                out[key] = v
        return out
    return {prefix: value} if prefix else {}


def _set_path(obj: Any, path: str, value: Any) -> None:
    tokens = _tokenize(path)
    if not tokens:
        return
    cur = obj
    for t in tokens[:-1]:
        if isinstance(t, int):
            if not isinstance(cur, list) or t >= len(cur):
                return  # path doesn't exist; silently skip
            cur = cur[t]
        else:
            if not isinstance(cur, dict) or t not in cur:
                return
            cur = cur[t]
    last = tokens[-1]
    if isinstance(last, int):
        if isinstance(cur, list) and last < len(cur):
            cur[last] = value
    else:
        if isinstance(cur, dict):
            cur[last] = value


def apply_overrides(spec: dict, overrides: dict | None) -> dict:
    """Return a deep-copied spec with user overrides written to their hook targets.

    ``overrides`` may be nested (``{"date_range": {"start": "..."}}``) or
    flat (``{"date_range.start": "..."}``) — both are accepted. Keys not
    declared in ``filter_hooks`` are ignored, so a dashboard can broadcast
    a filter that only some cards honour.
    """
    merged = copy.deepcopy(spec)
    hooks = merged.get("filter_hooks") or {}
    if not overrides or not hooks:
        return merged
    flat = _flatten(overrides)
    for override_key, value in flat.items():
        target = hooks.get(override_key)
        if not target:
            continue
        _set_path(merged, target, value)
    return merged
