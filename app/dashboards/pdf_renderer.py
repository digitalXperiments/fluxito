"""
Dashboard PDF Renderer (headless Chromium / Playwright)

On-demand PDF generation for dashboards. Used by:
  1. Share PDF button on /live-dashboards/{slug}
  2. Scheduled report worker for email/Slack attachments

Pipeline:
  1. Build the internal print URL for the live dashboard view (?print=1)
  2. Launch headless Chromium and authenticate it as the requesting user
     (Share PDF: the caller's ``uid`` cookie; scheduler: a freshly minted
     owner cookie) so private dashboards render
  3. Navigate; the page hydrates its own cards via the same /data endpoint
     the browser uses, then signals ``window.__pdfReady``
  4. Capture the rendered page (charts and all) to PDF via ``page.pdf()``

Why Chromium and not WeasyPrint: the live dashboard draws its charts with
ECharts (JavaScript). WeasyPrint has no JS engine, so it could only ever
produce a stripped-down value-grid report. Rendering the real page in a
real browser makes the export look like the dashboard the user sees.

Notes:
  * Chromium is installed into the image (see Dockerfile). If it is missing
    we raise RuntimeError so the route can return a clean 503.
  * Zero disk persistence: the PDF lives in memory only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.uid_cookie import sign_uid
from app.config import settings
from app.models.dashboard import Dashboard, DashboardCard

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
    Produce a PDF for ``dashboard`` by rendering the live view in Chromium.

    Args:
      db                — an open AsyncSession (caller owns its lifecycle).
      dashboard         — the loaded Dashboard ORM row.
      filter_params     — optional dict with any of:
                            {"start_date": "YYYY-MM-DD",
                             "end_date":   "YYYY-MM-DD"}.
                          (Platform filtering is not applied — the export
                          mirrors the live dashboard, which shows all cards.)
      include_insights  — unused; kept for call-site compatibility.
      base_url          — unused for rendering; the renderer always reaches
                          the app on its internal URL. Kept for compatibility.
      cookies           — the caller's request cookies. If a valid ``uid`` is
                          present it authenticates the headless browser;
                          otherwise an owner cookie is minted (scheduler path).

    Returns:
      PdfRenderResult with in-memory PDF bytes.

    Raises:
      RuntimeError — if Chromium/Playwright is unavailable, or the render
                     times out / fails.
    """
    filter_params = filter_params or {}
    generated_at = datetime.utcnow()

    # Card count is informational (response headers). Cheap COUNT, no hydration
    # — the browser hydrates the cards itself when it loads the page.
    count_q = (
        select(func.count()).select_from(DashboardCard).where(DashboardCard.dashboard_id == dashboard.id)
    )
    card_count = int((await db.execute(count_q)).scalar_one() or 0)

    # ── Authenticate the headless browser ────────────────────────────────────
    # Prefer the caller's own signed uid cookie (Share PDF button). With no
    # request context (scheduler), mint one for the dashboard owner so private
    # dashboards still render.
    uid_cookie = (cookies or {}).get("uid")
    if not uid_cookie and dashboard.user_id is not None:
        uid_cookie = sign_uid(str(dashboard.user_id))

    # ── Build the internal print URL ─────────────────────────────────────────
    internal = settings.INTERNAL_BASE_URL.rstrip("/")
    query: dict[str, str] = {"print": "1"}
    start = filter_params.get("start_date") or ""
    end = filter_params.get("end_date") or ""
    if start:
        query["start"] = start
    if end:
        query["end"] = end
    url = f"{internal}/live-dashboards/{dashboard.share_slug}?{urlencode(query)}"

    pdf_bytes = await _render_via_chromium(
        url=url,
        origin=internal,
        uid_cookie=uid_cookie,
        timeout_s=settings.PDF_RENDER_TIMEOUT_S,
    )

    return PdfRenderResult(
        pdf_bytes=pdf_bytes,
        card_count=card_count,
        live_card_count=card_count,  # the page renders live; best-effort count
        generated_at=generated_at,
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


async def _render_via_chromium(
    *,
    url: str,
    origin: str,
    uid_cookie: str | None,
    timeout_s: int,
) -> bytes:
    """Launch headless Chromium, render ``url`` to PDF, return the bytes.

    Imported lazily so the module imports cleanly in environments without
    Playwright installed (tests, scripts).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise RuntimeError(
            "Playwright is not installed. Dashboard PDF export needs the "
            "'playwright' package and its Chromium browser (added in the "
            "Dockerfile runtime stage)."
        ) from exc

    timeout_ms = max(5, timeout_s) * 1000

    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    # --no-sandbox: required to run Chromium as the non-root
                    # app user inside the container.
                    # --disable-dev-shm-usage: /dev/shm is tiny in containers;
                    # forces Chromium to use /tmp and avoids crashes on big pages.
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            except Exception as exc:  # browser binary missing / launch failure
                raise RuntimeError(f"Could not launch Chromium for PDF rendering: {exc}") from exc

            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 1600},
                    device_scale_factor=2,  # crisp text/charts in the PDF
                )
                if uid_cookie:
                    await context.add_cookies([{"name": "uid", "value": uid_cookie, "url": origin}])
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                # The view hydrates its cards asynchronously, then sets the flag.
                await page.wait_for_function("window.__pdfReady === true", timeout=timeout_ms)
                # Let ECharts paint its final frame before capture.
                await page.wait_for_timeout(700)

                # Render with screen media so the PDF matches the live view
                # (page.pdf() defaults to print media).
                await page.emulate_media(media="screen")
                return await page.pdf(
                    format="A4",
                    print_background=True,
                    scale=0.62,
                    margin={
                        "top": "10mm",
                        "bottom": "12mm",
                        "left": "8mm",
                        "right": "8mm",
                    },
                )
            finally:
                await browser.close()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Dashboard PDF render failed: {exc}") from exc
