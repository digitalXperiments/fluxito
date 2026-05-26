"""
Dashboard PDF Renderer

On-demand PDF generation for dashboards. Used by:
  1. Share PDF button on /live/{slug}
  2. Scheduled report worker for email/Slack attachments

Pipeline:
  1. Load Dashboard + DashboardCard rows from DB
  2. Apply date/platform filters
  3. Hydrate cards via MCP tool registry dispatch
  4. Render to HTML via Jinja template (print-optimized CSS)
  5. Convert HTML→PDF via WeasyPrint

Performance notes:
  * 20-card dashboard: ~75s worst case (serialized API calls)
  * Zero disk persistence: HTML/PDF in memory only

Known limitations:
  * No Chart.js rendering (WeasyPrint doesn't support JS)
  * METRIC cards render as value grids, TABLE as HTML tables
  * SVG chart pre-rendering is a future enhancement
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboards.hydration import card_to_payload, hydrate_dashboard_cards
from app.models.dashboard import Dashboard, DashboardCard
from app.templating import templates

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


@dataclass
class PdfRenderResult:
    """Result of a PDF render. ``pdf_bytes`` is never None on success."""

    pdf_bytes: bytes
    card_count: int
    live_card_count: int
    generated_at: datetime


async def render_dashboard_pdf(
    db: AsyncSession,
    dashboard: Dashboard,
    *,
    filter_params: dict[str, Any] | None = None,
    include_insights: bool = False,
    base_url: str = "",
    cookies: dict[str, str] | None = None,
) -> PdfRenderResult:
    """
    Produce a PDF for ``dashboard`` with the given filters applied.

    Cards are hydrated via the MCP tool registry (same path as the live
    batch query endpoint), then rendered via the pdf_layout Jinja template.

    Args:
      db                — an open AsyncSession (caller owns its lifecycle)
      dashboard         — the loaded Dashboard ORM row
      filter_params     — optional dict with any of:
                            {"start_date": "YYYY-MM-DD",
                             "end_date":   "YYYY-MM-DD",
                             "platforms":  ["ga4", "meta", ...]}
      include_insights  — unused; kept for call-site compatibility.
      base_url          — public URL for the app (for the footer link).

    Returns:
      PdfRenderResult with in-memory PDF bytes.

    Raises:
      RuntimeError — if WeasyPrint is not importable (missing system libs).
    """
    filter_params = filter_params or {}
    generated_at = datetime.utcnow()

    # ── card-based mode: hydrate cards then render via pdf_layout template ────
    cards_result = await db.execute(
        select(DashboardCard)
        .where(DashboardCard.dashboard_id == dashboard.id)
        .order_by(DashboardCard.position)
    )
    cards = list(cards_result.scalars().all())

    platforms_filter = filter_params.get("platforms") or []
    platforms_allowed = {p.strip().lower() for p in platforms_filter if p and p.strip()}
    if platforms_allowed:
        cards = [c for c in cards if (c.platform or "").lower() in platforms_allowed]

    date_filter = None
    if filter_params.get("start_date") or filter_params.get("end_date"):
        date_filter = {
            "start_date": filter_params.get("start_date") or "",
            "end_date": filter_params.get("end_date") or "",
        }
    await hydrate_dashboard_cards(dashboard, cards, date_filter=date_filter)

    card_payloads = [card_to_payload(c) for c in cards]
    live_count = sum(1 for c in cards if getattr(c, "_is_live", False))

    html = templates.get_template("dashboards/pdf_layout.html").render(
        dash={
            "title": dashboard.title,
            "description": dashboard.description or "",
            "insights": "",
            "share_slug": dashboard.share_slug,
        },
        cards=card_payloads,
        filter_params={
            "start_date": filter_params.get("start_date") or "",
            "end_date": filter_params.get("end_date") or "",
            "platforms": sorted(platforms_allowed) if platforms_allowed else [],
        },
        generated_at=generated_at,
        base_url=base_url or "",
        card_count=len(cards),
        live_card_count=live_count,
    )

    pdf_bytes = await asyncio.to_thread(_html_to_pdf_bytes, html, base_url)

    return PdfRenderResult(
        pdf_bytes=pdf_bytes,
        card_count=len(cards),
        live_card_count=live_count,
        generated_at=generated_at,
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
#
# Card hydration and payload normalisation are in ``app/dashboards/hydration.py``
# so the scheduler worker can reuse them without importing WeasyPrint.


def _html_to_pdf_bytes(html: str, base_url: str) -> bytes:
    """
    Synchronous WeasyPrint call. Intended to run in a thread via
    ``asyncio.to_thread`` — WeasyPrint is CPU-bound and blocks the
    event loop if called directly.

    Imported lazily so module import works in environments (tests,
    scripts) that don't have the pango/cairo shared libraries installed.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        # ImportError: weasyprint not installed.
        # OSError:     shared libs (libpango / libcairo) missing at dlopen.
        raise RuntimeError(
            "WeasyPrint is not available in this environment. "
            "On Debian/Ubuntu, install: libpango-1.0-0 libpangoft2-1.0-0 "
            "libharfbuzz0b libharfbuzz-subset0. In Docker, these are "
            "added in the runtime stage of the Dockerfile."
        ) from exc

    # base_url is used by WeasyPrint to resolve any relative asset URLs
    # inside the HTML (images, stylesheets). For our template everything
    # is inlined so this is informational.
    return HTML(string=html, base_url=base_url or None).write_pdf()
