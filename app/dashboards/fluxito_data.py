"""Platform data helper copied into every hosted Streamlit working directory.

This module is the only supported way for a model-authored app to refresh
live data. It talks to Fluxito's data plane with a runtime token. It never
contains OAuth tokens, Fernet keys, or database URLs.

The hosted process receives:

  FLUXITO_DATA_URL          POST target for query()
  FLUXITO_RUNTIME_TOKEN     bearer for that POST
  FLUXITO_DASHBOARD_ID      uuid (informational)
  FLUXITO_CONNECTION_ALIASES  JSON list of {alias, type, status}

Do not rewrite this file in the artifact — the host overwrites it on every
start so the contract stays under platform control.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any

_DATA_URL = os.environ.get("FLUXITO_DATA_URL", "")
_TOKEN = os.environ.get("FLUXITO_RUNTIME_TOKEN", "")
_ALIASES_RAW = os.environ.get("FLUXITO_CONNECTION_ALIASES", "[]")


def connections() -> list[dict[str, Any]]:
    """Bound aliases for this dashboard (no secrets)."""
    try:
        data = json.loads(_ALIASES_RAW)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def default_range(days: int = 30) -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=max(1, int(days)))
    return start, end


def query(
    alias: str,
    action: str,
    params: dict[str, Any] | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    """Run a live query through Fluxito using a bound connection alias.

    The host resolves ``alias`` to a stored connection. You cannot pass a
    tool name — Fluxito chooses the tool from the binding.

    Returns a dict. On failure the dict has ``error=True`` and ``message``.
    Never raises for transport/tool errors — Streamlit pages should check
    ``result.get("error")``.
    """
    if not _DATA_URL or not _TOKEN:
        return {
            "error": True,
            "error_type": "not_hosted",
            "message": "fluxito_data.query only works inside a Fluxito-hosted dashboard.",
        }
    if not _DATA_URL.startswith(("http://", "https://")):
        return {
            "error": True,
            "error_type": "not_hosted",
            "message": "FLUXITO_DATA_URL must be an http(s) URL.",
        }
    body = json.dumps(
        {
            "alias": alias,
            "action": action,
            "params": params or {},
        }
    ).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 — scheme checked above
        _DATA_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("error", True)
                return payload
        except Exception:
            pass
        return {
            "error": True,
            "error_type": "http_error",
            "message": f"Data plane HTTP {exc.code}: {exc.reason}",
        }
    except Exception as exc:
        return {"error": True, "error_type": "transport", "message": str(exc)[:300]}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": True, "error_type": "bad_response", "message": "Data plane returned non-JSON."}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def as_dataframe(result: dict[str, Any]):
    """Best-effort tabular conversion. Uses pandas if installed, else list[dict]."""
    rows = _rows_from_result(result)
    try:
        import pandas as pd  # type: ignore

        return pd.DataFrame(rows)
    except Exception:
        return rows


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """Sum numeric columns / first-row metrics for st.metric."""
    if not isinstance(result, dict) or result.get("error"):
        return {}
    if isinstance(result.get("metrics"), dict):
        return {str(k): v for k, v in result["metrics"].items()}
    rows = _rows_from_result(result)
    if not rows:
        return {}
    out: dict[str, Any] = {}
    first = rows[0]
    numeric_keys = [
        k for k, v in first.items() if k not in {"date", "dimension", "dimensions"} and _is_number(v)
    ]
    if len(rows) == 1:
        return {k: first[k] for k in numeric_keys}
    for k in numeric_keys:
        total = 0.0
        for row in rows:
            try:
                total += float(row.get(k) or 0)
            except (TypeError, ValueError):
                pass
        out[k] = int(total) if total.is_integer() else round(total, 2)
    return out


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).replace(",", "").replace("%", ""))
        return True
    except (TypeError, ValueError):
        return False


def _rows_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    if isinstance(result.get("rows"), list) and result["rows"] and isinstance(result["rows"][0], dict):
        return list(result["rows"])
    # GA4-shaped: dimension_headers + metric_headers + rows of values
    dim_h = result.get("dimension_headers") or result.get("dimensions")
    met_h = result.get("metric_headers") or result.get("metrics")
    raw_rows = result.get("rows") or result.get("data") or []
    if isinstance(dim_h, list) and isinstance(met_h, list) and isinstance(raw_rows, list):
        out = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            dims = row.get("dimension_values") or row.get("dimensions") or []
            mets = row.get("metric_values") or row.get("metrics") or []
            item: dict[str, Any] = {}
            for name, val in zip(dim_h, dims, strict=False):
                item[str(name)] = val
            for name, val in zip(met_h, mets, strict=False):
                item[str(name)] = val
            if item:
                out.append(item)
        if out:
            return out
    if isinstance(result.get("data"), list):
        return [r for r in result["data"] if isinstance(r, dict)]
    return []
