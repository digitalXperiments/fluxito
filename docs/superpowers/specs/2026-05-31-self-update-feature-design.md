# Fluxito Self-Update & Automated Release — Design Spec

**Date:** 2026-05-31
**Branch:** `update_feature`
**Status:** Approved design — ready for implementation planning

## Problem

Self-hosters install Fluxito via `git clone` + Docker and have no in-product way to
know a newer version exists or to update to it. There is also no automated release
pipeline — versions are inconsistent across the repo and GitHub releases are cut by
hand.

This design delivers two coupled halves:

1. **Release automation** — every meaningful merge to `main` automatically cuts a
   versioned, multi-arch container image published to GHCR plus a GitHub release.
2. **In-product self-update** — a version badge for all users, an "update available"
   cue and one-click update for the super-admin, executed by a privileged sidecar
   that pulls the new image and recreates the app container.

## Goals

- Version number displayed prominently in the UI for every user.
- Super-admin sees an "update available" indicator (Claude-Desktop style) and can
  apply the update with one click, with automatic rollback on failure.
- Updates delivered as pre-built GHCR images (primary path); build-from-source
  remains documented as the secondary path for developers/forkers.
- Releases are fully automated from merges to `main`; patch auto-increments, minor
  bumps are a one-file manual edit.
- The internet-facing web app never holds Docker socket access.

## Non-Goals

- Auto-applying updates on a schedule (updates are always super-admin initiated).
- Updating the `db`/`redis`/`nginx` sidecars (only the `app` image is swapped;
  infra images are pinned and updated via normal compose pulls).
- Multi-node / orchestrated (k8s, swarm) update flows. Target is single-host
  `docker compose`.

---

## Part A — Versioning & Release Automation

### A1. Single source of truth

Current drift: `pyproject.toml` = `1.0.2`, `app/config.py` `MCP_SERVER_VERSION` =
`1.0.3`, latest git tag = `v1.0.2`. Resolved as follows:

- **`VERSION` file at repo root holds the minor track only**, e.g. `1.0`.
  Human-controlled. Editing it (`1.0` → `1.1`) is the *only* manual version action.
- **Git tags are the patch ledger.** The CI computes the next patch as
  `max(existing v<track>.* tag patch) + 1`, or `0` if the track has no tags yet
  (handles a fresh minor bump → `1.1.0`).
- **The exact full version is baked into the image at build time** via
  `--build-arg APP_VERSION=<full>`. The Dockerfile persists it (env var
  `APP_VERSION` and/or `app/_version.txt`).
- **Runtime resolution** (`app/_version.py`, new module):
  1. `APP_VERSION` env (set in released images) — authoritative.
  2. Fallback for source/dev builds: `<VERSION-file track>.0+local`.
- `config.MCP_SERVER_VERSION` and all UI/version surfaces read from this module.
  The hardcoded `"1.0.3"` constant is removed.
- `pyproject.toml` version is made dynamic from the `VERSION` track (or pinned to
  the track for tooling; exact released number is not authored there).

Given tags stop at `v1.0.2` and `VERSION` track is `1.0`, the first auto-release is
`v1.0.3` — consistent with the existing CHANGELOG/config.

### A2. Release workflow (`.github/workflows/release.yml`)

**Trigger:** `push` to `main`, with `paths-ignore` for docs/markdown
(`**/*.md`, `docs/**`, `CHANGELOG.md`, `README.md`, license/notice files).

**Concurrency:** a single group (e.g. `release-main`) with no cancel-in-progress,
so overlapping merges serialize and never race on tags.

**Steps:**
1. **Skip check** — if the head commit message contains `[skip release]`, exit 0.
2. **Compute version** — read `VERSION` track; `git fetch --tags`; find highest
   `v<track>.*` tag; `patch = max+1` (or `0` if none). `FULL = <track>.<patch>`.
3. **Build & push** — `docker buildx` multi-arch (`linux/amd64`, `linux/arm64`),
   `--build-arg APP_VERSION=<FULL>`, tags `ghcr.io/digitalxperiments/fluxito:<FULL>`
   and `:latest`, pushed to GHCR. Image labeled with the version (OCI labels).
4. **Tag** — create and push annotated git tag `v<FULL>`.
5. **GitHub Release** — create release `v<FULL>` with **auto-generated notes**
   (merged PRs + contributors since previous tag). `make_latest: true`.

**Permissions:** `contents: write` (tags + releases), `packages: write` (GHCR).
GHCR auth via the built-in `GITHUB_TOKEN`.

**No commit-back to `main`** — VERSION is only touched by humans, so the workflow
never pushes commits and there is no self-trigger loop and no VERSION merge
conflicts.

### A3. Minor/major bumps

Edit `VERSION` (`1.0` → `1.1` for minor, `2.0` for major) in a normal commit/PR.
On merge, the track has no tags yet → patch starts at `0` → release `1.1.0`.

---

## Part B — In-Product Self-Update

### B1. Version display (all users)

In `app/templates/base.html` sidebar header (next to the wordmark / Beta pill), add a
small `v1.0.x` chip rendered from the runtime version module. Visible to everyone.
For the **super-admin only**, when an update is available the chip gains a subtle
colored dot — the "something new" cue.

### B2. Detection — update-check service

New backend service (e.g. `app/services/update_service.py`):

- Queries GitHub Releases API:
  `GET https://api.github.com/repos/digitalXperiments/fluxito/releases/latest`.
- Semver-compares the release tag against the running full version.
- **Caches the result in Redis** with a ~6h TTL (respects the 60/hr unauthenticated
  rate limit; avoids hammering on every page load).
- Controlled by an instance setting **`update_checks_enabled`** (default on). When
  off, no outbound calls are made (air-gapped installs).
- All failures are swallowed/logged — never block or error the UI.

**Endpoint:** `GET /api/updates/status` →
`{ current, latest, update_available, release_notes_url, published_at, checks_enabled }`.
Readable by any authenticated user (drives the badge); the trigger endpoint is
separately gated (B4).

### B3. Update panel (super-admin)

Lives under `/admin`. Shows current → latest, the rendered release-notes body from
the GitHub release, and an **"Update now"** button behind a confirmation that warns
Fluxito will briefly restart. Hidden/forbidden for non-super-admins.

### B4. The engine — `fluxito-updater` sidecar

New service in `docker-compose.yml`:

- Small image with the Docker CLI + the `docker-compose.yml` mounted read-only and
  `/var/run/docker.sock` mounted.
- **Internal Docker network only — no host port, never proxied by nginx.**
- Tiny HTTP API guarded by a shared secret **`UPDATER_TOKEN`** (also injected into
  the `app` service so it can authenticate calls):
  - `POST /update { version }` — begin an update to the target version.
  - `GET /status` — current job state.
- **Update procedure:**
  1. Record the current version as `previous` (for rollback).
  2. Set target `FLUXITO_VERSION=<version>` in the managed env file.
  3. `docker compose pull app` — **pull before recreate**; a failed pull never
     touches the running app.
  4. `docker compose up -d app` — recreate; the new container's `start.sh` runs
     `alembic upgrade head` automatically, then serves.
  5. Poll the app healthcheck up to a timeout.
  6. **On success:** record success + new version. **On failure (unhealthy within
     timeout):** revert `FLUXITO_VERSION` to `previous`, `up -d app`, record failure
     with reason.
- Writes job progress/status to **Redis** so state survives the app restart.
- Supports a **`--dry-run`** mode (pull only, no recreate) for safe manual testing.

### B5. Update flow (handling the self-restart)

The app serving the UI is the thing being restarted, so the flow is designed around
that:

1. Super-admin clicks **Update now** → app authenticates to the updater and `POST`s
   the target version, then returns immediately.
2. UI flips to an "Updating…" state and polls `GET /api/updates/status` (which reads
   the job state from Redis).
3. The app container is recreated; the polling endpoint is briefly unavailable — the
   UI tolerates this with retries/backoff.
4. New app instance comes up; `/api/updates/status` reports the job result from
   Redis → UI shows **"Updated to v1.0.x ✓"** (or the failure + rollback notice).

The UI never talks to the updater directly (it isn't reachable through nginx); all
status flows app → Redis → app → UI.

### B6. Compose & delivery-model change ⚠️ (affects existing installs)

In `docker-compose.yml`:
- `app` service: `build: .` → `image: ghcr.io/digitalxperiments/fluxito:${FLUXITO_VERSION:-latest}`.
- **Remove the `./app:/app/app` bind mount** — it would shadow the updated image's
  code and silently defeat updates. (The `./docs:/app/docs:ro` mount can stay or be
  dropped; not load-bearing for updates.)
- Keep a `build: .` path for developers under a compose **profile** (e.g.
  `--profile build`) or a documented `docker-compose.build.yml` override.
- Add the `updater` service (B4) and the `UPDATER_TOKEN` / `FLUXITO_VERSION` env
  plumbing.

This is the one change that affects existing self-hosters: they pull the new compose
file once to switch from build-from-source to image-pull. Documented as a migration
note in README + CHANGELOG.

---

## Documentation (README)

Two install methods, **leading with Docker image pull** (recommended, works for
non-technical users):
1. **Docker (recommended):** pull the compose file + `docker compose up -d` →
   pulls the published GHCR image. Updates via the in-app button.
2. **Build from source (developers/forkers):** `git clone` + build profile.

Plus a short migration note for existing installs (the §B6 compose change) and a note
that `update_checks_enabled` can be disabled for air-gapped deployments.

---

## Failure Handling Summary

| Failure | Behavior |
|---|---|
| Update-check (GitHub API) fails | Silent; log only; UI shows current version, no badge |
| Image pull fails | Updater reports error; running app untouched (pull precedes recreate) |
| New container migration/boot fails healthcheck | Updater auto-reverts to previous version, `up -d`, reports failure + reason |
| Updater unreachable / token mismatch | App surfaces a clear error in the panel; no state change |
| GitHub API rate-limited | Served from Redis cache; check skipped until TTL |

## Security Notes

- Docker socket access is isolated to the `updater` sidecar only; the
  internet-facing `app` never mounts it.
- `updater` has no host port and is not proxied by nginx — reachable only on the
  internal Docker network.
- App↔updater calls authenticated with the shared `UPDATER_TOKEN`.
- Update trigger endpoint is super-admin-only (matches the existing `/admin` model);
  version-status read is available to any authenticated user.

## Testing

- **Unit:** semver compare/parse; runtime version resolution (env vs fallback);
  update-check service with mocked GitHub API + Redis cache (hit/miss/expired/error);
  `update_checks_enabled` off → no outbound call; `/api/updates/status` RBAC; trigger
  endpoint super-admin gating.
- **Integration / manual (documented procedure):** updater `--dry-run` pull; full
  update against a locally-tagged image; forced-failure path verifying auto-rollback.
- **CI dry-run:** release workflow version-computation logic validated on a branch
  (compute-only, no publish) before first real run.

## Implementation Ordering

The full one-click update (B4/B5) is in scope — not deferred. The natural build
order, to be detailed in the implementation plan, is:

1. **Part A** (release automation) first — nothing else works without published GHCR
   images to pull.
2. **B1–B3** (version display, detection, panel) — usable as soon as releases exist.
3. **B4–B6** (updater sidecar, self-restart flow, compose change) — the one-click
   engine, wired once images are validated.

This is sequencing within a single deliverable, not a scope reduction; all parts
ship.
