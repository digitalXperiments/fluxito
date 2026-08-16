"""Short-lived embed tokens for hosted web dashboards.

Minted on the Fluxito app origin for a logged-in viewer. Redeemed only on
the dash origin for POST /query. Never placed in the artifact or the iframe URL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.config import settings

EMBED_TTL_S = 30 * 60
_SIG_LEN = 32


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(body: str) -> str:
    return hmac.new(settings.APP_SECRET_KEY.encode(), body.encode("ascii"), hashlib.sha256).hexdigest()[
        :_SIG_LEN
    ]


def mint_embed_token(
    *,
    slug: str,
    dashboard_id: str,
    viewer_id: str,
    aliases: list[str] | None = None,
    ttl_s: int = EMBED_TTL_S,
) -> tuple[str, int]:
    exp = int(time.time()) + max(60, int(ttl_s))
    payload = {
        "v": 1,
        "slug": slug,
        "dashboard_id": str(dashboard_id),
        "viewer_id": str(viewer_id),
        "aliases": [a for a in (aliases or []) if a],
        "exp": exp,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_sign(body)}", ttl_s


def verify_embed_token(token: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    if not body or not sig or not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if exp < int(time.time()):
        return None
    slug = str(payload.get("slug") or "").strip()
    dashboard_id = str(payload.get("dashboard_id") or "").strip()
    if not slug or not dashboard_id:
        return None
    return payload
