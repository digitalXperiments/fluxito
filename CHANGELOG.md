# Changelog

All notable changes to Fluxito will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.4] — 2026-05-31

### Added
- In-product version display and one-click self-update for super-admins, delivered
  via published GHCR images and a privileged `fluxito-updater` sidecar with
  automatic rollback on failed updates.
- Automated push-to-main release pipeline: auto-incrementing patch versions,
  multi-arch (amd64/arm64) GHCR images, and auto-generated GitHub releases.
- `update_checks_enabled` instance setting (disable for air-gapped installs).

### Changed
- Default Docker deployment now pulls the published GHCR image; build-from-source
  moves behind `docker-compose.build.yml`.
- Version is now sourced from a single `VERSION` track file (+ build-time
  `APP_VERSION`), resolving prior drift between `pyproject.toml` and `config.py`.

### Removed
- `deploy/` folder removed from the public repo. Production orchestration (compose, update script, reverse-proxy config) is environment-specific and is kept outside version control. Self-hosting is covered by the root `docker-compose.yml` and the Production notes in the README (including the required nginx `/mcp` no-buffering config).

## [1.0.3] — 2026-05-30

### Added
- Solution Design Reference (SDR) v2: richer audit sections (Executive Summary, Gap Register, Conversion Audit, Consent, Roadmap), viewer tabs, full Excel export of every section, and storing + downloading the original source `.xlsx` (validated, 2MB cap).
- Access control: a super-admin role (first setup account), an admin panel at `/admin` (Users + Access Requests), and a request-access flow gated by the `require_access_approval` instance setting (default off — open signup unchanged for existing self-hosts).
- Per-user MCP rate limiting (super-admin configurable and exempt) with an admin Rate Limits control.
- Whitelabel branding: brand name / wordmark / accent settings, brand-aware chrome and invite emails, and an admin Branding tab.
- Marketing landing page at `/` for logged-out visitors (hero, problem, how-it-works, features, platforms, OSS sections, video slot), brand-aware with an overridable `og:description`.
- Auto-provisioned personal project for users with none; one-time temp credentials for invites and admin password resets (no SMTP required).
- Production deployment under `deploy/` that builds from a local source checkout on the host and deploys by pulling `main` (`deploy/update.sh`).

### Changed
- MCP active-project now resolves per call from Redis (fixes `no_active_project` in batched/parallel tool calls).
- CI is now a pure quality gate (lint, typecheck, test, build smoke); it no longer publishes container images.
- Connections de-duplicated to one card per account.

### Fixed
- SDR User-Properties table rendering; Members-tab native validation bubble overlap; account-takeover vectors in the register / invite / password-reset flows (never set or reset a password on an email that already has one).

### Removed
- GHCR image publishing (`publish-sha` / `publish-demo` jobs) and the `v*` tag trigger; the `demo/` folder is now the production `deploy/` stack.

## [1.0.2] — 2026-05-28

### Added
- DB-backed System Settings screen at `/settings/system` for post-bootstrap runtime settings, including SMTP, rate limits, Sentry, CORS, GCS, and tool flags.
- Public demo infrastructure with automated deployment and MCP access restrictions.
- Multi-arch Docker image builds (AMD64 + ARM64) via QEMU.

### Changed
- `.env.example` is now a minimal bootstrap contract: database, Redis, signing/encryption keys, public URL/MCP redirects, and the special-case Google OAuth app only.
- SMTP and rate-limit reads now prefer DB-backed system settings and fall back to deprecated env/default values.
- CI split into fast SHA-based image builds and stable release-triggered demo image publishing.

### Fixed
- Release workflow now uses `semantic-release version` (was incorrectly using `publish --tag` which never created GitHub Releases).

## [1.0.1] — 2026-05-25

### Changed
- OAuth app credentials are now **DB-only**. The `.env` fallback for per-platform `*_CLIENT_ID` / `*_CLIENT_SECRET` env vars has been removed; configuration happens exclusively via `/settings/integrations`. Existing self-hosters who relied on env vars need to re-enter the credentials in the UI.
- First-admin setup wizard no longer offers "Sign in with Google" — that path required env-based Google credentials. Email/password is the only option for the first admin; Google sign-in becomes available for subsequent users once Google OAuth is configured at `/settings/integrations`.
- README rewritten — tighter flow, added local-tunnel-via-ngrok guide for testing Claude.ai against a local instance, dropped technical sections (curious devs read the code).
- `CONTRIBUTING.md` slimmed — clearer scope for what we accept vs decline.
- All plan/quota limits removed — projects are fully unlimited in the open-source release.

### Added
- `.github/CODEOWNERS` — declares reviewer ownership; pairs with branch protection in repo settings to enforce maintainer approval.

### Removed
- `app/templates/{landing,features,platforms,docs,legal/*}.html` and `app/api/public_routes.py` — moved to `parked/` (gitignored). Marketing pages now live in a separate static-site repo.
- Internal/technical docs: `docs/{ARCHITECTURE,API_REFERENCE,CONNECTORS,DESIGN_SYSTEM,FEATURES,MIGRATIONS,SDR_FEATURE_SPEC,STRATEGY_AND_DECISIONS,TESTING,TOKEN_LIFECYCLE,TOOLS_REFERENCE}.md`. Tutorials and `DEPLOYMENT` / `TROUBLESHOOTING` remain. Devs interested in internals can read the source.
- `scripts/import_env_oauth_apps.py` — obsolete with env-fallback gone.

### Fixed
- `/auth/signout` link in the user menu was 404'ing — now correctly links to `/signout`.
- Anonymous users are now redirected to sign-in from `/activity-log`, `/templates`, and `/live-dashboards` pages.
- Private dashboard query API routes gated behind session auth.
- `/setup` and `/api/` routes exempted from CSRF to fix setup wizard and API calls.
- Alembic multi-head migration repaired (039_repair_platform_indexes).

## [1.0.0] — initial open-source release

### Added
- 15 marketing/analytics platform connectors via MCP: GA4, GTM, Google Ads, Search Console, BigQuery, Snowflake, Redshift, Adobe Analytics, Adobe Launch, Amplitude, Meta Ads, TikTok Ads, Snap Ads, LinkedIn Ads, Pinterest Ads.
- Web UI for self-hosters at `/connect`, `/dashboards`, `/sdr`, `/automations`.
- Per-install OAuth-app credential management at `/settings/integrations` (admin-gated).
- First-run `/setup` wizard for creating the initial admin (email/password or Google sign-in).
- Auto-generated `APP_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` on first boot, written to `.env.local` (gitignored).
- Docker Compose stack with healthchecked Postgres and Redis services.
- One-click deploy templates for Render (`render.yaml`) and Railway (`railway.json`).
- Apache 2.0 license + standard project files (NOTICE, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT).

### Changed (versus the internal predecessor)
- OAuth client credentials moved from `.env` to a database-backed UI; env-based config remains as a sysadmin fallback.
- Single rate-limit tier (was per-plan tiers).

### Removed (versus the internal predecessor)
- Stripe billing integration, plan tiers, quota enforcement.
- Platform-admin panel and the `admin_role` user column.
- Fly.io-specific deployment configuration.

[Unreleased]: https://github.com/digitalXperiments/fluxito/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/digitalXperiments/fluxito/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/digitalXperiments/fluxito/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/digitalXperiments/fluxito/releases/tag/v1.0.0
