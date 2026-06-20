"""Scope fingerprinting and authorization for dashboard live-query refreshes.

Each dashboard stores a list of ``query_scopes`` in JSONB — authoritative
allowlist of what (platform, resource) pairs its batch-query endpoint may
reach. When a refresh comes in, we:

  1. Extract the resource fingerprint from the card's tool params
     (per-platform; ``_FINGERPRINT_EXTRACTORS`` below).
  2. Check each ``query_scopes`` entry for a match — an entry matches if
     every key it specifies is equal to the same key in the fingerprint.
     Missing keys on the scope entry act as wildcards.

A scope entry always has ``platform``. Any additional keys constrain the
match. Examples::

    {"platform": "ga4", "property_id": "279951751"}
    {"platform": "bigquery", "connection_id": "uuid"}
    {"platform": "bigquery", "connection_id": "uuid", "dataset_id": "analytics"}
    {"platform": "warehouse"}     # any warehouse query, any connection
"""

from __future__ import annotations

from collections.abc import Callable

# Fingerprint extractor signature: (params: dict) -> dict[str, str]
# Returns the resource identity of a single card's tool-call params.

FingerprintExtractor = Callable[[dict], dict]


def _ga4(params: dict) -> dict:
    # GA4 property ids arrive as "properties/123" or bare "123"; normalize to bare.
    pid = str(params.get("property_id") or "").strip()
    if pid.startswith("properties/"):
        pid = pid.split("/", 1)[1]
    return {"platform": "ga4", "property_id": pid}


def _bigquery(params: dict) -> dict:
    # BigQuery reads rely on a connection_id + optional dataset/project.
    return {
        "platform": "bigquery",
        "connection_id": str(params.get("connection_id") or ""),
        "project_id": params.get("project_id") or None,
        "dataset_id": params.get("dataset_id") or None,
    }


def _warehouse_sql(params: dict) -> dict:
    # Raw SQL via warehouse_query — the only handle we reliably have is the
    # connection_id. Finer-grained auth (per table/dataset) would require
    # SQL parsing, which we skip for now.
    return {
        "platform": "warehouse",
        "connection_id": str(params.get("connection_id") or ""),
    }


def _redshift(params: dict) -> dict:
    return {
        "platform": "redshift",
        "connection_id": str(params.get("connection_id") or ""),
    }


def _snowflake(params: dict) -> dict:
    return {
        "platform": "snowflake",
        "connection_id": str(params.get("connection_id") or ""),
    }


def _amplitude(params: dict) -> dict:
    return {
        "platform": "amplitude",
        "connection_id": str(params.get("connection_id") or ""),
        "project_id": params.get("project_id") or None,
    }


def _mixpanel(params: dict) -> dict:
    return {
        "platform": "mixpanel",
        "connection_id": str(params.get("connection_id") or ""),
        "project_id": params.get("project_id") or None,
    }


def _posthog(params: dict) -> dict:
    return {
        "platform": "posthog",
        "connection_id": str(params.get("connection_id") or ""),
        "project_id": params.get("project_id") or None,
    }


def _adobe_analytics(params: dict) -> dict:
    return {
        "platform": "adobe_analytics",
        "connection_id": str(params.get("connection_id") or ""),
        "report_suite_id": params.get("report_suite_id") or None,
    }


def _meta(params: dict) -> dict:
    return {
        "platform": "meta",
        "ad_account_id": str(params.get("ad_account_id") or params.get("account_id") or ""),
    }


def _tiktok(params: dict) -> dict:
    return {
        "platform": "tiktok",
        "advertiser_id": str(params.get("advertiser_id") or ""),
    }


def _snap(params: dict) -> dict:
    return {
        "platform": "snap",
        "ad_account_id": str(params.get("ad_account_id") or ""),
    }


def _google_ads(params: dict) -> dict:
    return {
        "platform": "google_ads",
        "customer_id": str(params.get("customer_id") or ""),
    }


def _search_console(params: dict) -> dict:
    return {
        "platform": "search_console",
        "site_url": str(params.get("site_url") or ""),
    }


def _gtm(params: dict) -> dict:
    # GTM rarely powers a card data query, but keep the slot available.
    return {
        "platform": "gtm",
        "account_id": str(params.get("account_id") or ""),
        "container_id": str(params.get("container_id") or ""),
    }


_FINGERPRINT_EXTRACTORS: dict[str, FingerprintExtractor] = {
    "ga4": _ga4,
    "bigquery": _bigquery,
    "warehouse": _warehouse_sql,
    "redshift": _redshift,
    "snowflake": _snowflake,
    "amplitude": _amplitude,
    "mixpanel": _mixpanel,
    "posthog": _posthog,
    "adobe_analytics": _adobe_analytics,
    "meta": _meta,
    "tiktok": _tiktok,
    "snap": _snap,
    "google_ads": _google_ads,
    "search_console": _search_console,
    "gtm": _gtm,
}


def fingerprint(platform: str, params: dict) -> dict:
    """Return the resource fingerprint for a card's ``(platform, params)``.

    Unknown platforms fall back to ``{"platform": platform}``, meaning
    only the platform itself is considered for scope matching. Add an
    extractor above when a new connector needs finer-grained scoping.
    """
    extractor = _FINGERPRINT_EXTRACTORS.get(platform)
    if not extractor:
        return {"platform": platform}
    return extractor(params)


def is_authorized(scopes: list[dict], platform: str, params: dict) -> bool:
    """True if any entry in ``scopes`` covers the given ``(platform, params)``.

    A scope entry matches when every non-empty key it declares equals the
    same key in the card's fingerprint. Missing keys on the scope entry
    are wildcards.
    """
    if not scopes:
        return False
    fp = fingerprint(platform, params)
    for scope in scopes:
        if scope.get("platform") != fp.get("platform"):
            continue
        match = True
        for key, want in scope.items():
            if key == "platform" or want in (None, ""):
                continue
            if fp.get(key) != want:
                match = False
                break
        if match:
            return True
    return False
