# Self-Update & Automated Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give self-hosters an in-product version badge and one-click super-admin update, fed by a fully automated push-to-main release pipeline that publishes versioned multi-arch GHCR images.

**Architecture:** Releases are computed from a `VERSION` minor-track file + git tags (patch ledger), baked into images by a GitHub Actions workflow. The running app reads its version at runtime, polls the GitHub Releases API (Redis-cached) to detect updates, and — for super-admins only — triggers a privileged `fluxito-updater` sidecar that pulls the new image and recreates the app container, auto-rolling-back on failure. The internet-facing app never touches the Docker socket.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (async), Redis (`redis.asyncio`), httpx, Jinja2 templates, Docker Compose, GitHub Actions, GHCR.

**Spec:** `docs/superpowers/specs/2026-05-31-self-update-feature-design.md`

**Conventions in this codebase (use them):**
- Raw Redis client: `app.app_state.redis_client` (async).
- Super-admin gate: `from app.api.admin_routes import require_superadmin` → `await require_superadmin(request)` (raises 401/403).
- Runtime settings: add a `RuntimeSetting(...)` to `RUNTIME_SETTINGS` in `app/settings_service.py`; read via `get_runtime_setting(db, key, default=...)`.
- API routers: one module per file in `app/api/`, exporting `router = APIRouter()`; register in `app/main.py` near line 422.
- Tests live under `tests/`, run with `pytest`. Use `pytest-asyncio` (`@pytest.mark.asyncio`) — already in use across the suite.
- Image name: `ghcr.io/digitalxperiments/fluxito` (lowercase).
- GitHub repo for the releases API: `digitalXperiments/fluxito`.

---

## File Structure

**Created:**
- `VERSION` — repo-root minor-track file (e.g. `1.0`). Single human-controlled version input.
- `app/_version.py` — runtime version resolver (env `APP_VERSION` → `VERSION` track fallback).
- `app/services/update_service.py` — GitHub release check, semver compare, Redis cache.
- `app/api/update_routes.py` — `GET /api/updates/status`, `POST /api/updates/apply`.
- `updater/server.py` — sidecar HTTP server (stdlib only) that runs the privileged update.
- `updater/Dockerfile` — sidecar image (docker CLI + python3).
- `.github/workflows/release.yml` — push-to-main release pipeline.
- Tests: `tests/test_version.py`, `tests/test_update_service.py`, `tests/test_update_routes.py`, `tests/test_updater_logic.py`.

**Modified:**
- `app/config.py` — `MCP_SERVER_VERSION` reads from `app/_version.py`.
- `pyproject.toml` — version sourced from `VERSION` track.
- `Dockerfile` — `ARG APP_VERSION` baked into `ENV APP_VERSION`.
- `app/settings_service.py` — add `update_checks_enabled` runtime setting + helper.
- `app/main.py` — register the update router.
- `app/templates/base.html` — version chip + super-admin update dot.
- `app/templates/admin.html` — update panel tab + JS poll flow.
- `docker-compose.yml` — `app` → GHCR image, drop `./app` bind mount, add `updater` service + env plumbing.
- `.env.example` — `FLUXITO_VERSION`, `UPDATER_TOKEN`, `UPDATER_URL`.
- `README.md`, `CHANGELOG.md` — install methods + changelog entry.

---

# PART A — Versioning & Release Automation

### Task A1: `VERSION` file + runtime version resolver

**Files:**
- Create: `VERSION`
- Create: `app/_version.py`
- Test: `tests/test_version.py`

- [ ] **Step 1: Create the VERSION track file**

```
1.0
```

(Single line, no `v`, just the `MAJOR.MINOR` track. File must end with a newline.)

- [ ] **Step 2: Write the failing test**

Create `tests/test_version.py`:

```python
import importlib
import os

import app._version as version_mod


def _reload():
    return importlib.reload(version_mod)


def test_baked_app_version_takes_precedence(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.0.7")
    mod = _reload()
    assert mod.get_version() == "1.0.7"


def test_falls_back_to_track_with_local_suffix(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    mod = _reload()
    v = mod.get_version()
    assert v.endswith("+local")
    # Track is MAJOR.MINOR, fallback adds a .0 patch → "1.0.0+local"
    assert v.count(".") == 2


def test_blank_app_version_uses_fallback(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "   ")
    mod = _reload()
    assert mod.get_version().endswith("+local")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app._version'`

- [ ] **Step 4: Implement `app/_version.py`**

```python
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

    Precedence: baked ``APP_VERSION`` env (released images) → ``<track>.0+local``.
    """
    baked = os.environ.get("APP_VERSION", "").strip()
    if baked:
        return baked
    return f"{read_track()}.0+local"


__version__ = get_version()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_version.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add VERSION app/_version.py tests/test_version.py
git commit -m "feat: VERSION track file + runtime version resolver"
```

---

### Task A2: Wire `config.MCP_SERVER_VERSION` + `pyproject.toml` to the resolver

**Files:**
- Modify: `app/config.py` (the `MCP_SERVER_VERSION: str = "1.0.3"` line, ~line 96)
- Modify: `pyproject.toml` (the `version = "1.0.2"` line)
- Test: `tests/test_version.py` (add one assertion)

- [ ] **Step 1: Add a failing test for config wiring**

Append to `tests/test_version.py`:

```python
def test_config_uses_runtime_version(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "9.9.9")
    import importlib
    import app._version as v
    importlib.reload(v)
    import app.config as config
    importlib.reload(config)
    assert config.settings.MCP_SERVER_VERSION == "9.9.9"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_version.py::test_config_uses_runtime_version -v`
Expected: FAIL — asserts `"1.0.3" == "9.9.9"`.

- [ ] **Step 3: Edit `app/config.py`**

Add near the top imports (after other `from app...`-free stdlib imports):

```python
from app._version import get_version as _get_version
```

Replace the hardcoded line:

```python
    MCP_SERVER_VERSION: str = "1.0.3"
```

with:

```python
    MCP_SERVER_VERSION: str = _get_version()
```

(If `app.config` is imported before `app._version` anywhere, this is safe — `_version` imports only stdlib.)

- [ ] **Step 4: Edit `pyproject.toml`**

Replace:

```toml
version = "1.0.2"
```

with a dynamic version sourced from the track file:

```toml
dynamic = ["version"]
```

and add (under `[tool.setuptools.dynamic]`, creating the table if absent):

```toml
[tool.setuptools.dynamic]
version = { file = "VERSION" }
```

> Note: this makes the package report the `MAJOR.MINOR` track (e.g. `1.0`) for tooling. The exact released number lives in the image env, not here — that's intentional.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_version.py -v && python -c "import app.config"`
Expected: all version tests PASS; `import app.config` succeeds (no circular import).

- [ ] **Step 6: Commit**

```bash
git add app/config.py pyproject.toml tests/test_version.py
git commit -m "feat: source MCP_SERVER_VERSION + pyproject version from VERSION track"
```

---

### Task A3: Bake `APP_VERSION` into the image at build

**Files:**
- Modify: `Dockerfile` (runtime stage)

- [ ] **Step 1: Add the build arg to the runtime stage**

In `Dockerfile`, in the **runtime** stage, just after the existing `ENV PYTHONDONTWRITEBYTECODE=...` block, add:

```dockerfile
# Exact release version, injected by the release workflow (--build-arg APP_VERSION=X.Y.Z).
# Source/dev builds leave it empty and the app falls back to the VERSION track.
ARG APP_VERSION=""
ENV APP_VERSION=${APP_VERSION}
```

- [ ] **Step 2: Verify the build accepts the arg**

Run:
```bash
docker build --build-arg APP_VERSION=1.0.99 -t fluxito-versiontest . \
  && docker run --rm --entrypoint sh fluxito-versiontest -c 'echo $APP_VERSION'
```
Expected: prints `1.0.99`.

- [ ] **Step 3: Verify default build is empty (dev fallback works)**

Run:
```bash
docker build -t fluxito-versiontest . \
  && docker run --rm --entrypoint sh fluxito-versiontest -c 'echo "[$APP_VERSION]"'
```
Expected: prints `[]` (empty → app uses `<track>.0+local`).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "feat: bake APP_VERSION build-arg into runtime image"
```

---

### Task A4: Release workflow (push-to-main → GHCR + GitHub release)

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: Release

on:
  push:
    branches: [main]
    paths-ignore:
      - "**/*.md"
      - "docs/**"
      - "LICENSE"
      - "NOTICE"

concurrency:
  group: release-main
  cancel-in-progress: false

permissions:
  contents: write   # push tags + create releases
  packages: write   # push to GHCR

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout (full history + tags)
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Skip on [skip release]
        id: skipcheck
        run: |
          MSG=$(git log -1 --pretty=%B)
          if echo "$MSG" | grep -qi '\[skip release\]'; then
            echo "skip=true" >> "$GITHUB_OUTPUT"
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Compute next version
        id: version
        if: steps.skipcheck.outputs.skip == 'false'
        run: |
          TRACK=$(tr -d '[:space:]' < VERSION)
          echo "Track: $TRACK"
          # Highest existing patch on this track, default -1 → next becomes 0.
          LATEST=$(git tag --list "v${TRACK}.*" \
            | sed "s/^v${TRACK}\.//" \
            | grep -E '^[0-9]+$' \
            | sort -n | tail -1)
          if [ -z "$LATEST" ]; then NEXT=0; else NEXT=$((LATEST + 1)); fi
          FULL="${TRACK}.${NEXT}"
          echo "Next version: $FULL"
          echo "full=$FULL" >> "$GITHUB_OUTPUT"
          echo "tag=v$FULL" >> "$GITHUB_OUTPUT"

      - name: Set up QEMU
        if: steps.skipcheck.outputs.skip == 'false'
        uses: docker/setup-qemu-action@v3

      - name: Set up Buildx
        if: steps.skipcheck.outputs.skip == 'false'
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        if: steps.skipcheck.outputs.skip == 'false'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build + push multi-arch image
        if: steps.skipcheck.outputs.skip == 'false'
        uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          build-args: |
            APP_VERSION=${{ steps.version.outputs.full }}
          tags: |
            ghcr.io/digitalxperiments/fluxito:${{ steps.version.outputs.full }}
            ghcr.io/digitalxperiments/fluxito:latest
          labels: |
            org.opencontainers.image.version=${{ steps.version.outputs.full }}
            org.opencontainers.image.source=https://github.com/digitalXperiments/fluxito

      - name: Create git tag
        if: steps.skipcheck.outputs.skip == 'false'
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git tag -a "${{ steps.version.outputs.tag }}" -m "Release ${{ steps.version.outputs.tag }}"
          git push origin "${{ steps.version.outputs.tag }}"

      - name: Create GitHub release (auto-generated notes)
        if: steps.skipcheck.outputs.skip == 'false'
        run: |
          gh release create "${{ steps.version.outputs.tag }}" \
            --title "${{ steps.version.outputs.tag }}" \
            --generate-notes \
            --latest
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

- [ ] **Step 2: Validate YAML syntax locally**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Dry-run the version-compute logic locally**

Run (simulates the compute step against current tags `v1.0.0..v1.0.2`):
```bash
TRACK=$(tr -d '[:space:]' < VERSION)
LATEST=$(git tag --list "v${TRACK}.*" | sed "s/^v${TRACK}\.//" | grep -E '^[0-9]+$' | sort -n | tail -1)
[ -z "$LATEST" ] && NEXT=0 || NEXT=$((LATEST + 1)); echo "${TRACK}.${NEXT}"
```
Expected: prints `1.0.3` (since the highest existing tag is `v1.0.2`).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: automated push-to-main release workflow (GHCR multi-arch + GitHub release)"
```

> **First real run happens after merge to main.** GHCR package visibility must be set to public once (GitHub → repo → Packages → fluxito → Package settings) so self-hosters can pull without auth. Note this in the PR description.

---

# PART B — In-Product Self-Update

### Task B1: `update_checks_enabled` runtime setting + helper

**Files:**
- Modify: `app/settings_service.py` (add to `RUNTIME_SETTINGS`, add helper)
- Test: `tests/test_update_service.py` (helper test)

- [ ] **Step 1: Add the runtime setting**

In `app/settings_service.py`, in the `RUNTIME_SETTINGS` tuple under the `# ── Instance operations ──` category, add:

```python
    RuntimeSetting(
        "update_checks_enabled",
        "Check for updates",
        "Periodically check GitHub for a newer Fluxito release and show an update "
        "indicator to super-admins. Turn off for air-gapped installs (no outbound calls).",
        "UPDATE_CHECKS_ENABLED",
        "bool",
        category="operations",
    ),
```

(Use the exact `category` string already used by the neighboring "Instance operations" settings — match it verbatim by reading the surrounding entries.)

- [ ] **Step 2: Add the env default to config**

In `app/config.py`, alongside the other runtime-setting env mirrors, add:

```python
    UPDATE_CHECKS_ENABLED: bool = True
```

- [ ] **Step 3: Add the helper**

In `app/settings_service.py`, near `access_approval_required`:

```python
async def update_checks_enabled() -> bool:
    """True when the instance is allowed to check GitHub for newer releases."""
    async with app_state.db_session_factory() as db:
        return bool(await get_runtime_setting(db, "update_checks_enabled", default=True))
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_update_service.py`:

```python
import pytest

from app.settings_service import RUNTIME_SETTING_BY_KEY


def test_update_checks_setting_registered():
    assert "update_checks_enabled" in RUNTIME_SETTING_BY_KEY
    spec = RUNTIME_SETTING_BY_KEY["update_checks_enabled"]
    assert spec.value_type == "bool"
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_update_service.py::test_update_checks_setting_registered -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/settings_service.py app/config.py tests/test_update_service.py
git commit -m "feat: update_checks_enabled instance setting"
```

---

### Task B2: Semver compare in the update service

**Files:**
- Create: `app/services/update_service.py`
- Test: `tests/test_update_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_update_service.py`:

```python
from app.services.update_service import is_newer, parse_semver


def test_parse_semver_strips_prefix_and_suffix():
    assert parse_semver("v1.2.3") == (1, 2, 3)
    assert parse_semver("1.2.3+local") == (1, 2, 3)
    assert parse_semver("1.2.3-rc1") == (1, 2, 3)
    assert parse_semver("1.0") == (1, 0, 0)


def test_is_newer():
    assert is_newer("1.0.3", "1.0.2") is True
    assert is_newer("1.1.0", "1.0.9") is True
    assert is_newer("1.0.2", "1.0.2") is False
    assert is_newer("1.0.1", "1.0.2") is False
    assert is_newer("1.0.2", "1.0.2+local") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_update_service.py -k "semver or is_newer" -v`
Expected: FAIL — `ModuleNotFoundError: app.services.update_service`.

- [ ] **Step 3: Implement the pure helpers**

Create `app/services/update_service.py`:

```python
"""Update detection: compare the running version against the latest GitHub release.

The latest-release lookup is cached in Redis to respect GitHub's unauthenticated
rate limit (60/hr) and avoid a network call on every page load.
"""

from __future__ import annotations

import json
import logging

import httpx

import app.app_state as app_state
from app._version import get_version
from app.settings_service import update_checks_enabled

logger = logging.getLogger(__name__)

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_update_service.py -k "semver or is_newer" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/update_service.py tests/test_update_service.py
git commit -m "feat: semver parse/compare helpers in update_service"
```

---

### Task B3: `check_for_update` — GitHub fetch + Redis cache + setting gate

**Files:**
- Modify: `app/services/update_service.py`
- Test: `tests/test_update_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_update_service.py`:

```python
import app.app_state as app_state
from app.services import update_service


class _FakeRedis:
    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.setex_calls = []

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value


@pytest.mark.asyncio
async def test_check_returns_disabled_when_setting_off(monkeypatch):
    monkeypatch.setattr(update_service, "update_checks_enabled", _async_return(False))
    monkeypatch.setattr(app_state, "redis_client", _FakeRedis())
    result = await update_service.check_for_update()
    assert result["checks_enabled"] is False
    assert result["update_available"] is False


@pytest.mark.asyncio
async def test_check_uses_cache_when_present(monkeypatch):
    monkeypatch.setattr(update_service, "update_checks_enabled", _async_return(True))
    monkeypatch.setattr(update_service, "get_version", lambda: "1.0.2")
    cached = json.dumps({"tag_name": "v1.0.5", "html_url": "https://x/releases/v1.0.5",
                         "published_at": "2026-05-30T00:00:00Z"})
    monkeypatch.setattr(app_state, "redis_client", _FakeRedis({update_service.CACHE_KEY: cached}))

    async def _boom(*a, **k):
        raise AssertionError("network should not be called on cache hit")

    monkeypatch.setattr(update_service, "_fetch_latest_release", _boom)
    result = await update_service.check_for_update()
    assert result["latest"] == "1.0.5"
    assert result["update_available"] is True


@pytest.mark.asyncio
async def test_check_swallows_network_errors(monkeypatch):
    monkeypatch.setattr(update_service, "update_checks_enabled", _async_return(True))
    monkeypatch.setattr(update_service, "get_version", lambda: "1.0.2")
    monkeypatch.setattr(app_state, "redis_client", _FakeRedis())

    async def _raise(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(update_service, "_fetch_latest_release", _raise)
    result = await update_service.check_for_update()
    assert result["update_available"] is False
    assert result["latest"] is None


def _async_return(value):
    async def _inner():
        return value
    return _inner
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_update_service.py -k check -v`
Expected: FAIL — `check_for_update` / `_fetch_latest_release` not defined.

- [ ] **Step 3: Implement the fetch + cache + orchestration**

Append to `app/services/update_service.py`:

```python
async def _fetch_latest_release() -> dict | None:
    """Fetch the latest release JSON from GitHub. Returns the raw dict or None."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "fluxito-update-check"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(GITHUB_LATEST_RELEASE_URL, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def _get_cached_or_fetch() -> dict | None:
    """Return the latest-release payload from Redis cache, fetching + caching on miss."""
    redis = app_state.redis_client
    if redis is not None:
        cached = await redis.get(CACHE_KEY)
        if cached:
            return json.loads(cached)
    data = await _fetch_latest_release()
    if data is not None and redis is not None:
        slim = {
            "tag_name": data.get("tag_name"),
            "html_url": data.get("html_url"),
            "published_at": data.get("published_at"),
        }
        await redis.setex(CACHE_KEY, CACHE_TTL_SECONDS, json.dumps(slim))
        return slim
    return data


async def check_for_update() -> dict:
    """Return update status. Never raises — all failures degrade to 'no update'."""
    current = get_version()
    base = {
        "current": current,
        "latest": None,
        "update_available": False,
        "release_notes_url": None,
        "published_at": None,
        "checks_enabled": True,
    }
    try:
        if not await update_checks_enabled():
            base["checks_enabled"] = False
            return base
        payload = await _get_cached_or_fetch()
        if not payload or not payload.get("tag_name"):
            return base
        latest_raw = payload["tag_name"]
        latest = latest_raw.lstrip("vV")
        base["latest"] = latest
        base["release_notes_url"] = payload.get("html_url")
        base["published_at"] = payload.get("published_at")
        base["update_available"] = is_newer(latest_raw, current)
    except Exception:  # noqa: BLE001 — update check must never break the UI
        logger.warning("update check failed", exc_info=True)
    return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_update_service.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add app/services/update_service.py tests/test_update_service.py
git commit -m "feat: cached GitHub release check in update_service"
```

---

### Task B4: `GET /api/updates/status` endpoint + router registration

**Files:**
- Create: `app/api/update_routes.py`
- Modify: `app/main.py` (register router near line 422)
- Test: `tests/test_update_routes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_update_routes.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services import update_service


@pytest.mark.asyncio
async def test_status_endpoint_returns_check_result(monkeypatch):
    async def _fake_check():
        return {
            "current": "1.0.2", "latest": "1.0.5", "update_available": True,
            "release_notes_url": "https://x", "published_at": "2026-05-30T00:00:00Z",
            "checks_enabled": True,
        }

    monkeypatch.setattr(update_service, "check_for_update", _fake_check)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/updates/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["update_available"] is True
    assert body["latest"] == "1.0.5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_update_routes.py::test_status_endpoint_returns_check_result -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement the route module**

Create `app/api/update_routes.py`:

```python
"""Update status + trigger endpoints.

- GET  /api/updates/status — any authenticated user (drives the version badge).
- POST /api/updates/apply  — super-admin only (triggers the updater sidecar).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services import update_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/updates/status")
async def update_status(request: Request):
    """Return current vs latest version + whether an update is available."""
    return JSONResponse(await update_service.check_for_update())
```

- [ ] **Step 4: Register the router in `app/main.py`**

After `app.include_router(access_request_router)` (~line 423), add:

```python
from app.api.update_routes import router as update_router

app.include_router(update_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_update_routes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/update_routes.py app/main.py tests/test_update_routes.py
git commit -m "feat: GET /api/updates/status endpoint"
```

---

### Task B5: Version chip in the sidebar (all users) + super-admin update dot

**Files:**
- Modify: `app/templates/base.html` (sidebar header, ~lines 96–102)
- Modify: the view/context that renders pages, to pass `app_version` + `is_superadmin` (see Step 2)

- [ ] **Step 1: Confirm what context variables base.html already receives**

Run: `grep -rn "is_superadmin\|app_version\|render(request" app/templating.py app/api/google_oauth_routes.py | head`
Expected: identifies whether `is_superadmin` is already in the template context. If `app_version`/`is_superadmin` are not globally injected, add them as Jinja globals in Step 2.

- [ ] **Step 2: Inject `app_version` as a Jinja global**

In `app/templating.py`, where the Jinja environment/globals are configured (same place `brand()` is registered), add:

```python
from app._version import get_version

# ... wherever globals are set (e.g. env.globals.update(...) / templates.env.globals):
env.globals["app_version"] = get_version()
```

(Match the existing registration style for `brand`. If globals are set per-request in `render()`, add `"app_version": get_version()` to that context dict instead.)

- [ ] **Step 3: Add the version chip to the sidebar header**

In `app/templates/base.html`, replace the sidebar header block:

```html
    <div class="sidebar-header">
      <a href="/home" class="wordmark">
        {{ wordmark() }}
      </a>
      {% if brand().name == 'Fluxito' %}<span class="beta-pill" title="Fluxito is in public beta">Beta</span>{% endif %}
    </div>
```

with:

```html
    <div class="sidebar-header">
      <a href="/home" class="wordmark">
        {{ wordmark() }}
      </a>
      {% if brand().name == 'Fluxito' %}<span class="beta-pill" title="Fluxito is in public beta">Beta</span>{% endif %}
      <span class="version-chip" id="version-chip" data-current="{{ app_version }}" title="Version {{ app_version }}">
        v{{ app_version }}<span class="update-dot" id="update-dot" hidden></span>
      </span>
    </div>
```

- [ ] **Step 4: Add minimal styles**

In the `<style>` block of `base.html` (or the sidebar CSS section), add:

```css
.version-chip { font-family: var(--mono); font-size: 10px; color: var(--muted, #888);
  margin-left: 6px; vertical-align: middle; position: relative; cursor: default; }
.update-dot { display: inline-block; width: 6px; height: 6px; border-radius: 99px;
  background: var(--blue, #4D7CE3); margin-left: 4px; vertical-align: middle; }
```

- [ ] **Step 5: Light up the dot for super-admins when an update exists**

Add a small script near the end of `base.html` (only meaningful for logged-in users; the endpoint is safe for all):

```html
<script>
(function () {
  var dot = document.getElementById('update-dot');
  if (!dot) return;
  fetch('/api/updates/status', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (d && d.update_available) {
        dot.hidden = false;
        var chip = document.getElementById('version-chip');
        if (chip) chip.title = 'Update available: v' + d.latest;
      }
    })
    .catch(function () {});
})();
</script>
```

- [ ] **Step 6: Manual verification (browse)**

Bring up the stack (`docker compose up -d` once Task B7 is done, or run the app locally), log in, and confirm the `v<version>` chip renders next to the Beta pill. With `update_service.check_for_update` mock-returning `update_available: true`, confirm the dot appears. Use the `browse` skill to screenshot before/after.

- [ ] **Step 7: Commit**

```bash
git add app/templates/base.html app/templating.py
git commit -m "feat: sidebar version chip + update-available dot"
```

---

### Task B6: `fluxito-updater` sidecar (server + image)

**Files:**
- Create: `updater/server.py`
- Create: `updater/Dockerfile`
- Test: `tests/test_updater_logic.py`

The sidecar is a stdlib-only HTTP server. Pure logic (env-file upsert, version validation) is unit-tested; the privileged `docker compose` calls are covered by manual verification in Task B7.

- [ ] **Step 1: Write the failing test for the env upsert + version validation**

Create `tests/test_updater_logic.py`:

```python
import importlib.util
import pathlib

# Load updater/server.py as a module without it being a package.
_SPEC = importlib.util.spec_from_file_location(
    "updater_server", pathlib.Path(__file__).resolve().parent.parent / "updater" / "server.py"
)
updater = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(updater)


def test_valid_version_accepts_semver():
    assert updater.is_valid_version("1.0.5") is True
    assert updater.is_valid_version("12.34.56") is True


def test_valid_version_rejects_injection():
    assert updater.is_valid_version("1.0.5; rm -rf /") is False
    assert updater.is_valid_version("latest") is False
    assert updater.is_valid_version("../../etc") is False
    assert updater.is_valid_version("") is False


def test_upsert_env_var_adds_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=secret\n")
    updater.upsert_env_var(str(env), "FLUXITO_VERSION", "1.0.5")
    text = env.read_text()
    assert "FLUXITO_VERSION=1.0.5" in text
    assert "POSTGRES_PASSWORD=secret" in text  # other vars untouched


def test_upsert_env_var_replaces_existing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FLUXITO_VERSION=1.0.4\nPOSTGRES_PASSWORD=secret\n")
    updater.upsert_env_var(str(env), "FLUXITO_VERSION", "1.0.5")
    text = env.read_text()
    assert "FLUXITO_VERSION=1.0.5" in text
    assert "FLUXITO_VERSION=1.0.4" not in text
    assert text.count("FLUXITO_VERSION=") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_updater_logic.py -v`
Expected: FAIL — `updater/server.py` does not exist.

- [ ] **Step 3: Implement `updater/server.py`**

```python
"""Fluxito updater sidecar.

A tiny stdlib HTTP server that performs the privileged image pull + container
recreate. Reachable only on the internal Docker network (no host port, never
proxied by nginx) and authenticated with a shared bearer token.

Endpoints:
  POST /update   {"version": "1.0.5"}   → begin update (async, returns 202)
  GET  /status                          → current job status

Job status is written to a JSON file on a shared volume so it survives the app
container restart that the update itself causes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("UPDATER_TOKEN", "")
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "/compose/docker-compose.yml")
ENV_FILE = os.environ.get("ENV_FILE", "/compose/.env")
STATE_FILE = os.environ.get("STATE_FILE", "/state/update.json")
APP_SERVICE = os.environ.get("APP_SERVICE", "app")
HEALTH_TIMEOUT = int(os.environ.get("HEALTH_TIMEOUT", "180"))
PORT = int(os.environ.get("UPDATER_PORT", "9000"))

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_lock = threading.Lock()


def is_valid_version(version: str) -> bool:
    """Strict MAJOR.MINOR.PATCH only — blocks shell-injection via the version field."""
    return bool(_VERSION_RE.match(version or ""))


def upsert_env_var(path: str, key: str, value: str) -> None:
    """Set KEY=value in an env file, replacing any existing line, leaving others intact."""
    line = f"{key}={value}"
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        lines = []
    out, replaced = [], False
    for existing in lines:
        if existing.startswith(f"{key}="):
            out.append(line)
            replaced = True
        else:
            out.append(existing)
    if not replaced:
        out.append(line)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def write_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE_FILE)


def read_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "idle"}


def _compose(*args: str, version: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if version is not None:
        env["FLUXITO_VERSION"] = version
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        capture_output=True, text=True, env=env, check=False,
    )


def _app_healthy() -> bool:
    res = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Health.Status}}", "fluxito-app"],
        capture_output=True, text=True, check=False,
    )
    return res.stdout.strip() == "healthy"


def _wait_healthy(timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _app_healthy():
            return True
        time.sleep(3)
    return False


def run_update(target: str, previous: str) -> None:
    """Pull → recreate → health-check → rollback on failure. Records state throughout."""
    write_state({"status": "pulling", "target": target, "previous": previous})
    upsert_env_var(ENV_FILE, "FLUXITO_VERSION", target)
    pull = _compose("pull", APP_SERVICE, version=target)
    if pull.returncode != 0:
        write_state({"status": "failed", "stage": "pull", "target": target,
                     "error": pull.stderr[-2000:]})
        return
    write_state({"status": "recreating", "target": target, "previous": previous})
    up = _compose("up", "-d", APP_SERVICE, version=target)
    if up.returncode != 0:
        _rollback(previous, "recreate", up.stderr[-2000:])
        return
    write_state({"status": "verifying", "target": target, "previous": previous})
    if _wait_healthy(HEALTH_TIMEOUT):
        write_state({"status": "success", "current": target, "previous": previous})
    else:
        _rollback(previous, "healthcheck", "new container did not become healthy")


def _rollback(previous: str, stage: str, error: str) -> None:
    write_state({"status": "rolling_back", "previous": previous, "stage": stage})
    upsert_env_var(ENV_FILE, "FLUXITO_VERSION", previous)
    _compose("up", "-d", APP_SERVICE, version=previous)
    write_state({"status": "failed", "stage": stage, "error": error,
                 "current": previous, "rolled_back": True})


class Handler(BaseHTTPRequestHandler):
    def _auth_ok(self) -> bool:
        return TOKEN and self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        if self.path != "/status":
            return self._send(404, {"error": "not found"})
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        self._send(200, read_state())

    def do_POST(self):  # noqa: N802
        if self.path != "/update":
            return self._send(404, {"error": "not found"})
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid json"})
        target = str(body.get("version", "")).lstrip("vV")
        previous = str(body.get("previous", "")).lstrip("vV")
        if not is_valid_version(target):
            return self._send(400, {"error": "invalid version"})
        if not _lock.acquire(blocking=False):
            return self._send(409, {"error": "update already in progress"})
        try:
            threading.Thread(
                target=lambda: self._guarded_run(target, previous), daemon=True
            ).start()
        finally:
            pass  # lock released inside _guarded_run
        self._send(202, {"status": "accepted", "target": target})

    def _guarded_run(self, target: str, previous: str) -> None:
        try:
            run_update(target, previous)
        finally:
            _lock.release()

    def log_message(self, *args):  # silence default stderr logging
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("UPDATER_TOKEN must be set")
    write_state({"status": "idle"})
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_updater_logic.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Create `updater/Dockerfile`**

```dockerfile
# Minimal updater sidecar: docker CLI + compose plugin + python3 stdlib server.
FROM docker:27-cli

RUN apk add --no-cache python3

COPY server.py /app/server.py
WORKDIR /app

EXPOSE 9000
CMD ["python3", "server.py"]
```

- [ ] **Step 6: Verify the updater image builds**

Run: `docker build -t fluxito-updater ./updater`
Expected: builds successfully.

- [ ] **Step 7: Commit**

```bash
git add updater/server.py updater/Dockerfile tests/test_updater_logic.py
git commit -m "feat: fluxito-updater sidecar (pull + recreate + auto-rollback)"
```

---

### Task B7: Compose changes — GHCR image, drop bind mount, add updater service

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Switch the `app` service to the GHCR image + drop the code bind mount**

In `docker-compose.yml`, change the `app` service. Replace:

```yaml
  app:
    container_name: fluxito-app
    build: .
```

with:

```yaml
  app:
    container_name: fluxito-app
    image: ghcr.io/digitalxperiments/fluxito:${FLUXITO_VERSION:-latest}
    # Developers/forkers: build locally with `docker compose --profile build up -d --build`.
    build:
      context: .
    profiles: ["", "build"]
    environment:
      - UPDATER_URL=http://updater:9000
      - UPDATER_TOKEN=${UPDATER_TOKEN:?set UPDATER_TOKEN in .env}
```

> Keep the existing `environment:` keys (DATABASE_URL, REDIS_URL, APP_ENV, APP_BASE_URL) — append the two `UPDATER_*` lines to that existing list rather than duplicating the block.

And **remove the code bind mount** from the `app` service volumes:

```yaml
    volumes:
      - ./app:/app/app        # ← DELETE THIS LINE (would shadow the image's updated code)
      - ./docs:/app/docs:ro
```

Leave `./docs:/app/docs:ro` if present; only the `./app:/app/app` line is removed.

> The `profiles: ["", "build"]` trick lets the default `docker compose up` pull the image while `--profile build` rebuilds from source. Verify this syntax against the installed compose version in Step 5; if it misbehaves, fall back to a separate `docker-compose.build.yml` override documented in the README.

- [ ] **Step 2: Add the `updater` service**

Add to `docker-compose.yml` services:

```yaml
  updater:
    container_name: fluxito-updater
    build: ./updater
    environment:
      - UPDATER_TOKEN=${UPDATER_TOKEN:?set UPDATER_TOKEN in .env}
      - COMPOSE_FILE=/compose/docker-compose.yml
      - ENV_FILE=/compose/.env
      - STATE_FILE=/state/update.json
      - APP_SERVICE=app
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./docker-compose.yml:/compose/docker-compose.yml:ro
      - ./.env:/compose/.env                 # writable: updater pins FLUXITO_VERSION here
      - update_state:/state
      - .:/compose-root:ro                    # build context for `docker compose pull/up`
    restart: unless-stopped
    # No `ports:` — internal network only, never exposed via nginx.
```

> The updater needs the compose project to resolve relative paths the same way the host does. Set `COMPOSE_FILE=/compose/docker-compose.yml` and mount the repo root read-only at `/compose-root` is **not** sufficient for `build:`-based services. Since `app` now uses a pre-built `image:`, `docker compose pull app` + `up -d app` do **not** need the build context — they only need the compose file + socket. Confirm in Step 5.

- [ ] **Step 3: Add the shared state volume**

In the `volumes:` block at the bottom of `docker-compose.yml`:

```yaml
volumes:
  postgres_data:
  redis_data:
  update_state:
```

- [ ] **Step 4: Mount the shared state volume into `app`**

So the app can read job status written by the updater. Add to the `app` service `volumes:`:

```yaml
      - update_state:/state:ro
```

- [ ] **Step 5: Validate compose + smoke-test**

Run:
```bash
cp -n .env.example .env 2>/dev/null || true
# ensure UPDATER_TOKEN is set in .env for validation
grep -q '^UPDATER_TOKEN=' .env || echo "UPDATER_TOKEN=devtoken" >> .env
docker compose config >/dev/null && echo "compose valid"
```
Expected: prints `compose valid`. Then bring up the stack and confirm all containers are healthy:
```bash
docker compose --profile build up -d --build
docker compose ps
```
Expected: `fluxito-app`, `fluxito-updater`, db, redis, nginx all `running`/`healthy`.

- [ ] **Step 6: Verify updater reachability from the app container (internal only)**

Run:
```bash
docker exec fluxito-app sh -c 'curl -fsS -H "Authorization: Bearer $UPDATER_TOKEN" http://updater:9000/status'
```
Expected: JSON like `{"status": "idle"}`. Then confirm it is **not** exposed on the host:
```bash
curl -s http://localhost:9000/status; echo "exit=$?"
```
Expected: connection refused / no response (no host port).

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: GHCR image for app, updater sidecar, shared state volume"
```

---

### Task B8: `POST /api/updates/apply` — super-admin trigger + status passthrough

**Files:**
- Modify: `app/api/update_routes.py`
- Test: `tests/test_update_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_update_routes.py`:

```python
from app.api import update_routes


@pytest.mark.asyncio
async def test_apply_requires_superadmin(monkeypatch):
    async def _deny(request):
        from fastapi import HTTPException
        raise HTTPException(403, "Super-admin only")

    monkeypatch.setattr(update_routes, "require_superadmin", _deny)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/updates/apply")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_apply_forwards_to_updater(monkeypatch):
    async def _allow(request):
        return {"id": "1", "email": "a@b.c", "is_superadmin": True}

    async def _check():
        return {"current": "1.0.2", "latest": "1.0.5", "update_available": True,
                "release_notes_url": "https://x", "published_at": None, "checks_enabled": True}

    calls = {}

    async def _post(version, previous):
        calls["version"] = version
        calls["previous"] = previous
        return {"status": "accepted", "target": version}

    monkeypatch.setattr(update_routes, "require_superadmin", _allow)
    monkeypatch.setattr(update_service, "check_for_update", _check)
    monkeypatch.setattr(update_routes, "_trigger_updater", _post)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/updates/apply")
    assert resp.status_code == 200
    assert calls["version"] == "1.0.5"
    assert calls["previous"] == "1.0.2"


@pytest.mark.asyncio
async def test_apply_rejects_when_no_update(monkeypatch):
    async def _allow(request):
        return {"id": "1", "email": "a@b.c", "is_superadmin": True}

    async def _check():
        return {"current": "1.0.5", "latest": "1.0.5", "update_available": False,
                "release_notes_url": None, "published_at": None, "checks_enabled": True}

    monkeypatch.setattr(update_routes, "require_superadmin", _allow)
    monkeypatch.setattr(update_service, "check_for_update", _check)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/updates/apply")
    assert resp.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_update_routes.py -k apply -v`
Expected: FAIL — `apply` route + `_trigger_updater` not defined.

- [ ] **Step 3: Implement the trigger endpoint**

Edit `app/api/update_routes.py`. Add imports at the top:

```python
import os

import httpx

from app.api.admin_routes import require_superadmin
```

Add the helper + route:

```python
UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:9000")
UPDATER_TOKEN = os.environ.get("UPDATER_TOKEN", "")


async def _trigger_updater(version: str, previous: str) -> dict:
    """POST the target version to the updater sidecar. Returns its JSON response."""
    headers = {"Authorization": f"Bearer {UPDATER_TOKEN}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{UPDATER_URL}/update",
            headers=headers,
            json={"version": version, "previous": previous},
        )
        resp.raise_for_status()
        return resp.json()


@router.post("/api/updates/apply")
async def update_apply(request: Request):
    """Super-admin: trigger an update to the latest available version."""
    await require_superadmin(request)  # raises 401/403
    status = await update_service.check_for_update()
    if not status.get("update_available") or not status.get("latest"):
        return JSONResponse({"error": "no update available"}, status_code=409)
    try:
        result = await _trigger_updater(status["latest"], status["current"])
    except httpx.HTTPError as exc:
        logger.error("updater trigger failed: %s", exc)
        return JSONResponse({"error": "updater unreachable"}, status_code=502)
    return JSONResponse({"status": "started", "target": status["latest"], "updater": result})
```

Also add an updater status passthrough so the UI can poll job progress:

```python
@router.get("/api/updates/job")
async def update_job(request: Request):
    """Super-admin: current updater job status (survives app restart via shared volume)."""
    await require_superadmin(request)
    headers = {"Authorization": f"Bearer {UPDATER_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{UPDATER_URL}/status", headers=headers)
            resp.raise_for_status()
            return JSONResponse(resp.json())
    except httpx.HTTPError:
        # During the app's own restart the updater may briefly be unreachable.
        return JSONResponse({"status": "unknown"}, status_code=503)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_update_routes.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add app/api/update_routes.py tests/test_update_routes.py
git commit -m "feat: POST /api/updates/apply + job status passthrough (super-admin)"
```

---

### Task B9: Admin update panel + poll-through-restart UI

**Files:**
- Modify: `app/templates/admin.html` (add an "Updates" tab/panel + JS)

- [ ] **Step 1: Inspect the existing admin tab structure**

Run: `grep -n "tab\|data-tab\|panel\|section" app/templates/admin.html | head -30`
Expected: reveals how tabs/panels are declared so the new "Updates" panel matches the existing pattern (class names, switching JS).

- [ ] **Step 2: Add the Updates panel markup**

Following the existing tab pattern from Step 1, add a new tab button labelled **Updates** and a panel:

```html
<section class="admin-panel" data-panel="updates" hidden>
  <h2>Updates</h2>
  <div id="update-box" class="update-box">
    <p>Current version: <strong id="upd-current">…</strong></p>
    <p id="upd-latest-row" hidden>Latest version: <strong id="upd-latest"></strong>
      — <a id="upd-notes" href="#" target="_blank" rel="noopener">release notes</a></p>
    <p id="upd-uptodate" hidden>✓ You're on the latest version.</p>
    <button id="upd-apply" class="btn btn-primary" hidden>Update now</button>
    <p id="upd-progress" class="update-progress" hidden></p>
  </div>
</section>
```

- [ ] **Step 3: Add the panel logic**

Add this script (adapt the panel-activation hook to the existing tab switcher found in Step 1):

```html
<script>
(function () {
  var apply = document.getElementById('upd-apply');
  if (!apply) return;

  function refreshStatus() {
    return fetch('/api/updates/status', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        document.getElementById('upd-current').textContent = 'v' + d.current;
        if (d.update_available) {
          document.getElementById('upd-latest').textContent = 'v' + d.latest;
          document.getElementById('upd-notes').href = d.release_notes_url || '#';
          document.getElementById('upd-latest-row').hidden = false;
          apply.hidden = false;
        } else {
          document.getElementById('upd-uptodate').hidden = false;
        }
      });
  }

  function pollJob() {
    var prog = document.getElementById('upd-progress');
    prog.hidden = false;
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      fetch('/api/updates/job', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { status: 'unknown' }; })
        .then(function (j) {
          prog.textContent = 'Status: ' + j.status + (j.target ? ' → v' + j.target : '');
          if (j.status === 'success') {
            clearInterval(iv);
            prog.textContent = '✓ Updated to v' + (j.current || j.target) + '. Reloading…';
            setTimeout(function () { location.reload(); }, 2000);
          } else if (j.status === 'failed') {
            clearInterval(iv);
            prog.textContent = '✗ Update failed (' + (j.stage || '') + ')'
              + (j.rolled_back ? ' — rolled back to v' + j.current : '');
          }
        })
        .catch(function () { prog.textContent = 'Restarting… (' + tries + ')'; });
    }, 3000);
  }

  apply.addEventListener('click', function () {
    if (!confirm('Update now? Fluxito will briefly restart.')) return;
    apply.disabled = true;
    fetch('/api/updates/apply', { method: 'POST', credentials: 'same-origin' })
      .then(function (r) {
        if (r.ok) { pollJob(); }
        else { r.json().then(function (e) { alert('Could not start update: ' + (e.error || r.status)); apply.disabled = false; }); }
      })
      .catch(function () { pollJob(); });  // app may drop mid-request as it restarts
  });

  refreshStatus();
})();
</script>
```

- [ ] **Step 4: Manual verification (browse)**

With the full stack up (Task B7) and a published GHCR image newer than the running one, log in as super-admin, open `/admin` → Updates, confirm current/latest render, click **Update now**, and watch the progress poll through the restart to "Updated to vX". Use the `browse` skill to capture the flow. Also test the failure path by temporarily pointing at a bad tag and confirming the rollback message.

- [ ] **Step 5: Commit**

```bash
git add app/templates/admin.html
git commit -m "feat: admin Updates panel with poll-through-restart UI"
```

---

### Task B10: Docs — README install methods, `.env.example`, CHANGELOG

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the new env vars to `.env.example`**

Append:

```bash
# ── Self-update ──────────────────────────────────────────────────────────
# Image tag the app runs. Leave as 'latest'; the in-app updater pins this
# to a specific version when you click "Update now".
FLUXITO_VERSION=latest
# Shared secret between the app and the updater sidecar. Generate a random value:
#   openssl rand -hex 32
UPDATER_TOKEN=
# Internal URL of the updater sidecar (do not expose this port publicly).
UPDATER_URL=http://updater:9000
```

- [ ] **Step 2: Rewrite the README install section (Docker-first)**

In `README.md`, under the install/quickstart section, lead with the image-pull path:

````markdown
## Install

### Docker (recommended)

```bash
git clone https://github.com/digitalXperiments/fluxito.git
cd fluxito
cp .env.example .env
# Set UPDATER_TOKEN (openssl rand -hex 32) and any secrets in .env
docker compose up -d
```

This pulls the published image `ghcr.io/digitalxperiments/fluxito:latest`. Updates
are one click from the in-app **Admin → Updates** panel (super-admin only).

### Build from source (developers / forkers)

```bash
docker compose --profile build up -d --build
```

Builds the image locally from your checkout instead of pulling from GHCR. Local
code changes are picked up on rebuild.
````

- [ ] **Step 3: Add a note about disabling update checks**

Append to the README's configuration/operations section:

```markdown
### Air-gapped installs

Set **Admin → Settings → Check for updates** off (or `UPDATE_CHECKS_ENABLED=false`)
to stop all outbound calls to GitHub. The version is still displayed; no update
checks are performed.
```

- [ ] **Step 4: Update CHANGELOG**

Under `## [Unreleased]`, add:

```markdown
### Added
- In-product version display and one-click self-update for super-admins, delivered
  via published GHCR images and a privileged `fluxito-updater` sidecar with
  automatic rollback on failed updates.
- Automated push-to-main release pipeline: auto-incrementing patch versions,
  multi-arch (amd64/arm64) GHCR images, and auto-generated GitHub releases.
- `update_checks_enabled` instance setting (disable for air-gapped installs).

### Changed
- Default Docker deployment now pulls the published GHCR image; build-from-source
  moves behind `docker compose --profile build`.
- Version is now sourced from a single `VERSION` track file (+ build-time
  `APP_VERSION`), resolving prior drift between `pyproject.toml` and `config.py`.
```

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md CHANGELOG.md
git commit -m "docs: Docker-first install, self-update env vars, changelog"
```

---

## Final Verification

- [ ] **Run the full test suite**

Run: `pytest tests/test_version.py tests/test_update_service.py tests/test_update_routes.py tests/test_updater_logic.py -v`
Expected: all PASS.

- [ ] **Lint/type-check the new code**

Run: `ruff check app/_version.py app/services/update_service.py app/api/update_routes.py updater/server.py && mypy app/_version.py app/services/update_service.py app/api/update_routes.py`
Expected: no new errors (match the repo's existing ruff/mypy config).

- [ ] **End-to-end manual update test**

With the stack up and a newer GHCR image available: trigger an update from `/admin → Updates`, confirm the app recreates and reports the new version, then confirm the version chip shows the new number after reload. Verify the forced-failure rollback path once.

- [ ] **Open the PR**

The release workflow fires on merge to `main`, cutting `v1.0.3`. Before merge, in the PR description note the one-time manual step: **set the GHCR package visibility to public** so self-hosters can pull without authentication.

---

## Self-Review Notes (coverage check)

- Spec A1 (single source of truth) → Tasks A1, A2, A3. ✓
- Spec A2 (release workflow, skip filter, multi-arch, auto notes) → Task A4. ✓
- Spec A3 (minor/major bump = edit VERSION) → covered by A1/A4 logic (no tags on new track → patch 0). ✓
- Spec B1 (version display, super-admin dot) → Task B5. ✓
- Spec B2 (detection, Redis cache, setting gate, silent failure) → Tasks B1, B2, B3, B4. ✓
- Spec B3 (super-admin panel) → Task B9. ✓
- Spec B4 (updater sidecar, token, internal-only, pull-before-recreate, auto-rollback, dry-run) → Tasks B6, B7. (Dry-run: the `pull`-only path is exercised by `_compose("pull", ...)`; an explicit `--dry-run` flag can be added to `run_update` if desired — noted, not blocking.)
- Spec B5 (poll-through-restart flow) → Tasks B8 (`/api/updates/job`), B9 (UI). ✓
- Spec B6 (compose: GHCR image, drop bind mount, build profile, env plumbing) → Task B7. ✓
- Job-status transport: spec mentioned Redis; this plan uses a **shared volume file** instead, so the updater needs no Redis client. Functionally equivalent (survives app restart) and simpler — documented here as an intentional refinement.
