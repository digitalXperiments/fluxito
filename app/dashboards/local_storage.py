"""
Local Dashboard Storage

Development-friendly storage for card scripts:
  {DASHBOARDS_LOCAL_DIR}/{user_id}/{dashboard_id}/{title}.py

Primary backend during development; GCS is production.
Both backends coexist: local is written first for easy debugging.

Configuration:
  DASHBOARDS_LOCAL_DIR — base directory (default: ~/.fluxito/dashboards)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _base_dir() -> Path:
    """Return the base directory for local dashboard storage."""
    from app.config import settings

    raw = getattr(settings, "DASHBOARDS_LOCAL_DIR", None) or ""
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".fluxito" / "dashboards"


def _dashboard_dir(user_id: str, dashboard_id: str) -> Path:
    return _base_dir() / user_id / dashboard_id


def save_dashboard_script(
    user_id: str,
    dashboard_id: str,
    script_content: str,
    dashboard_title: str = "",
) -> str:
    """
    Write the generated card script to local disk.

    Returns the absolute path of the saved file.
    Creates parent directories if they don't exist.
    """
    target_dir = _dashboard_dir(user_id, dashboard_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Sanitise title for use as a filename component
    safe_title = _safe_filename(dashboard_title) if dashboard_title else "dashboard"
    file_path = target_dir / f"{safe_title}.py"

    file_path.write_text(script_content, encoding="utf-8")
    logger.info("Saved dashboard script to %s", file_path)
    return str(file_path)


def load_dashboard_script(
    user_id: str,
    dashboard_id: str,
    dashboard_title: str = "",
) -> str | None:
    """Read a saved dashboard script from local disk. Returns None if not found."""
    target_dir = _dashboard_dir(user_id, dashboard_id)
    safe_title = _safe_filename(dashboard_title) if dashboard_title else "dashboard"
    file_path = target_dir / f"{safe_title}.py"

    if not file_path.exists():
        # Fallback: look for any .py in the dashboard dir
        py_files = list(target_dir.glob("*.py"))
        if py_files:
            file_path = sorted(py_files)[-1]
        else:
            return None

    try:
        return file_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read dashboard script %s: %s", file_path, exc)
        return None


def delete_dashboard_scripts(user_id: str, dashboard_id: str) -> None:
    """Delete all scripts for a dashboard directory."""
    target_dir = _dashboard_dir(user_id, dashboard_id)
    if not target_dir.exists():
        return
    try:
        import shutil

        shutil.rmtree(target_dir)
        logger.info("Deleted local dashboard dir %s", target_dir)
    except OSError as exc:
        logger.warning("Failed to delete local dashboard dir %s: %s", target_dir, exc)


def list_local_dashboards(user_id: str) -> list[dict]:
    """
    List all locally-saved dashboards for a user.
    Returns [{dashboard_id, file_path, title}] sorted newest-first by mtime.
    """
    user_dir = _base_dir() / user_id
    if not user_dir.exists():
        return []

    results = []
    for dash_dir in user_dir.iterdir():
        if not dash_dir.is_dir():
            continue
        py_files = sorted(dash_dir.glob("*.py"), key=lambda p: p.stat().st_mtime, reverse=True)
        if py_files:
            f = py_files[0]
            results.append(
                {
                    "dashboard_id": dash_dir.name,
                    "file_path": str(f),
                    "title": f.stem,
                }
            )

    results.sort(key=lambda x: os.path.getmtime(x["file_path"]), reverse=True)
    return results


def _safe_filename(s: str) -> str:
    """Convert a dashboard title into a safe filename component (max 60 chars)."""
    import re

    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:60] or "dashboard"
