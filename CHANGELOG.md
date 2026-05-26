# Changelog

All notable changes to Fluxito will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- DB-backed System Settings screen at `/settings/system` for post-bootstrap runtime settings, including SMTP, rate limits, Sentry, CORS, GCS, and tool flags.

### Changed
- `.env.example` is now a minimal bootstrap contract: database, Redis, signing/encryption keys, public URL/MCP redirects, and the special-case Google OAuth app only.
- SMTP and rate-limit reads now prefer DB-backed system settings and fall back to deprecated env/default values.

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

[Unreleased]: https://github.com/digitalXperiments/fluxito/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/digitalXperiments/fluxito/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/digitalXperiments/fluxito/releases/tag/v1.0.0
