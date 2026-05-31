"""Update detection: compare the running version against the latest GitHub release.

The latest-release lookup is cached in Redis to respect GitHub's unauthenticated
rate limit (60/hr) and avoid a network call on every page load.
"""

from __future__ import annotations

GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/digitalXperiments/fluxito/releases/latest"
)
CACHE_KEY = "update:latest_release"
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
HTTP_TIMEOUT = 5.0


def parse_semver(value: str) -> tuple[int, int, int]:
    """Parse 'vMAJOR.MINOR.PATCH' (with optional +/- suffix) into a comparable tuple."""
    cleaned = value.strip().lstrip("vV").split("+")[0].split("-")[0]
    parts = (cleaned.split(".") + ["0", "0", "0"])[:3]
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return (0, 0, 0)


def is_newer(latest: str, current: str) -> bool:
    """True if `latest` is a strictly higher semver than `current`."""
    return parse_semver(latest) > parse_semver(current)
