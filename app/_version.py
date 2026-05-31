"""Single runtime source of the running Fluxito version.

Released images bake the exact version in via the ``APP_VERSION`` build-arg
(set by the release workflow). Source/dev builds fall back to the minor
track in the repo-root ``VERSION`` file plus a ``+local`` suffix.
"""

from __future__ import annotations

import os
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def read_track() -> str:
    """Return the MAJOR.MINOR track from the repo-root VERSION file."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0"
    except OSError:
        return "0.0"


def get_version() -> str:
    """Resolve the running full version.

    Precedence: baked ``APP_VERSION`` env (released images) -> ``<track>.0+local``.
    """
    baked = os.environ.get("APP_VERSION", "").strip()
    if baked:
        return baked
    return f"{read_track()}.0+local"


__version__ = get_version()
