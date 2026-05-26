# Fluxito

**Open-source AI operations layer for marketing analytics. End-to-end — from tracking plan, to tag management implementation, to reporting and dashboards — through a conversation with any MCP-compatible AI.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status: 1.0](https://img.shields.io/badge/status-1.0-green)](CHANGELOG.md)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](Dockerfile)

---

## Why Fluxito

Running marketing analytics today means four separate jobs, usually owned by four different people or vendors:

1. **Define** what to track — events, parameters, user properties, conversions. Lives in a "tracking plan" Google Sheet that nobody updates.
2. **Implement** it — manually clicking through GTM, GA4, Meta Pixel, configuring tags / triggers / variables.
3. **Pipe** the data — warehouse syncs, custom dimensions, Looker connectors.
4. **Report** on it — build dashboards, run audits, surface anomalies, write the weekly recap deck.

Each step is slow, error-prone, and out of sync with the others. Tracking plans go stale. Implementations drift. Dashboards lie.

Fluxito collapses all four into a single conversation with your AI. **The AI is the operator; Fluxito gives it the hands.** It speaks the [Model Context Protocol](https://modelcontextprotocol.io) — an open standard — so it works with any MCP-compatible client: Claude, GPT, Gemini, or your own agent. Fluxito exposes a unified interface across **15 platforms**:

| Stage | Platforms |
|---|---|
| **Define / SDR** | Generate, browse, and version a Smart Data Reference (annotated tracking plan + data dictionary), exportable to Excel |
| **Implement** | Google Tag Manager, Adobe Launch — create/update/delete tags, triggers, variables; workspace-gated publishing |
| **Measure** | Google Analytics 4, Adobe Analytics, Amplitude, Search Console |
| **Acquire** | Google Ads, Meta, TikTok, Snap, LinkedIn, Pinterest |
| **Warehouse** | BigQuery, Snowflake, Redshift |
| **Report** | Native dashboards (card-native, JSON-defined, signed-URL share), scheduled reports via email + Slack, automations |

You self-host on Docker (or any container host). Tokens encrypted at rest. No data leaves your infrastructure except to the vendor APIs you authorise.

---

## What you can do with it

Example prompts that span the full lifecycle. Each one your AI actually executes against your real platforms once Fluxito is connected:

> *"Generate a tracking plan for our ecommerce checkout flow. List events, parameters, user properties. Save it as our SDR v1."*

> *"Implement those events in our GTM container. Create the tags, triggers, and dataLayer variables. Don't publish — leave them in a workspace for me to review."*

> *"Audit my GTM container `GTM-XXXXXXX` end-to-end. Flag anything broken, anything firing on the wrong trigger, and any GA4 events without conversion mapping."*

> *"Build a weekly dashboard with checkout funnel, paid-spend ROAS, SEO clicks, and CAC — all on one screen — and share me a public link."*

> *"Why don't my Google Ads conversions match GA4? Diagnose using both data sources and tell me where the gap is."*

> *"Every Monday at 9am, send me a Slack digest of last week's anomalies across GA4, Meta Ads, and Google Ads."*

You're not assembling these queries by clicking through dashboards. You're describing the outcome; the AI figures out which tools to call, in what order, against which connections — and runs them.

---

## Quickstart

Three commands. ~5 minutes from clone to running server.

### Prerequisites

- **Docker** + Docker Compose (Docker Desktop on Mac/Windows; native on Linux)
- A web browser

### 1. Clone and start

```bash
git clone https://github.com/digitalXperiments/fluxito.git
cd fluxito
docker compose up -d
```

That brings up Postgres, Redis, the Fluxito app, and an nginx reverse proxy on port `8000`. Migrations run automatically on first start. Internal secrets auto-generate — you don't edit anything.

### 2. Open `http://localhost:8000`

You'll be redirected to **`/setup`** because no admin account exists yet.

### 3. Create the first admin

Pick an email and password. (No verification email — self-host operators are expected to be the same person who started the container.)

After creating the admin, you land on **`/settings/integrations`**.

### 4. Configure at least one platform

Click **Configure** on a platform card. The modal walks you through registering an OAuth app with the vendor (Google, Meta, etc.), pasting the redirect URI Fluxito gives you, and saving the credentials back into Fluxito.

**Start with Google** — one OAuth client unlocks GA4, GTM, Google Ads, Search Console, and BigQuery. The walkthrough is at [docs/tutorials/google-cloud-setup.md](docs/tutorials/google-cloud-setup.md).

### 5. Connect a real account

Go to **`/connect`** and click the platform's connect button. Authorise the app against your real account.

### 6. Connect your AI

See the next section.

---

## Connecting your platforms

Each tutorial is a marketer-friendly walkthrough — how to register the OAuth app, copy the credentials, paste them into Fluxito's UI:

| Platform | Tutorial |
|---|---|
| **Google** (foundational — covers GA4, GTM, Ads, Search Console, BigQuery) | [google-cloud-setup.md](docs/tutorials/google-cloud-setup.md) |
| Google Analytics 4 | [google-analytics-4.md](docs/tutorials/google-analytics-4.md) |
| Google Tag Manager | [google-tag-manager.md](docs/tutorials/google-tag-manager.md) |
| Google Ads | [google-ads.md](docs/tutorials/google-ads.md) |
| Search Console | [search-console.md](docs/tutorials/search-console.md) |
| BigQuery | [bigquery.md](docs/tutorials/bigquery.md) |
| Meta Ads | [meta-ads.md](docs/tutorials/meta-ads.md) |
| TikTok Ads | [tiktok-ads.md](docs/tutorials/tiktok-ads.md) |
| LinkedIn Ads | [linkedin-ads.md](docs/tutorials/linkedin-ads.md) |
| Pinterest Ads | [pinterest-ads.md](docs/tutorials/pinterest-ads.md) |
| Snap Ads | [snap-ads.md](docs/tutorials/snap-ads.md) |
| Snowflake | [snowflake.md](docs/tutorials/snowflake.md) |
| Redshift | [redshift.md](docs/tutorials/redshift.md) |
| Amplitude | [amplitude.md](docs/tutorials/amplitude.md) |
| Adobe Analytics | [adobe-analytics.md](docs/tutorials/adobe-analytics.md) |
| Adobe Launch | [adobe-launch.md](docs/tutorials/adobe-launch.md) |

All credentials live in the database, encrypted at rest. **No `.env` fallback** — Settings → Integrations is the only configuration surface.

---

## Configuration

Fluxito has two distinct configuration surfaces:

1. **Environment variables** — loaded at startup by `app/config.py`. These are the only values the app needs before it can boot.
2. **Web UI** (`/settings/integrations` and `/settings/system` after the first admin signs in) — the normal place to configure OAuth apps and runtime settings.

### Must be set in the environment (foundational)

These are the only values that must be present before the app can start:

| Variable | Purpose | Notes |
|---|---|---|
| `APP_SECRET_KEY` | Signs session cookies and CSRF tokens | ≥32 chars. Rotate = everyone logged out. |
| `TOKEN_ENCRYPTION_KEY` | Encrypts all OAuth tokens at rest (Fernet) | **Never rotate without a re-encryption step** or all stored tokens become unreadable. |
| `DATABASE_URL` | Postgres connection | `postgresql+asyncpg://...` (prefix normalized automatically) |
| `REDIS_URL` | Redis connection | `redis://...` |
| `APP_BASE_URL` | Public URL used for OAuth redirects and MCP | Must be reachable by your AI client (or ngrok in dev) |
| `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` | The single Google OAuth app | Used for **both** user login **and** the Google family of connectors (GA4, GTM, Ads, Search Console, BigQuery) |
| `MCP_ALLOWED_REDIRECT_URIS` | Allowed callbacks for MCP clients | Comma-separated; defaults to Claude.ai's callback |

Runtime settings such as SMTP, GCS, Sentry, CORS allowed origins, and rate-limit tuning are managed in **Settings → System**. Existing env vars for those values are treated only as deprecated fallbacks until a DB setting is saved.

**Google is special.** Its OAuth app credentials live in the environment because the same app is used for user login and the Google connectors. Per-user Google tokens are still stored encrypted in the normal `oauth_connections` table (same as every other platform).

### Platform OAuth apps (Meta, TikTok, LinkedIn, Pinterest, Snap, Adobe, etc.)

**These are not configured via `.env` at all.**

After the first admin account exists, go to **Settings → Integrations**. Create the OAuth app in the vendor console, then paste the client ID + secret into Fluxito. The values are stored encrypted in the `oauth_app_credentials` table and read exclusively through `app.auth.oauth_app_credentials` (5-minute cache, no env fallback).

If a platform has no row, the Connect button is disabled and users see a "contact your admin" message.

See `app/auth/oauth_app_credentials.py` and the model `OAuthAppCredential` for the implementation. `.env.example` already documents this correctly.

---

## Connecting an AI client

Fluxito is an MCP server. Any client that speaks the [Model Context Protocol](https://modelcontextprotocol.io) can connect — Claude, GPT, Gemini, or a custom agent. The MCP endpoint is `/mcp`.

The AI client needs a publicly reachable URL. Two paths:

### A. Self-hosted on a real domain

If Fluxito is running at `https://fluxito.example.com`:

1. In your AI client, add a custom MCP connector / tool server.
2. Set the URL to `https://fluxito.example.com/mcp`.
3. The client redirects to your Fluxito instance for the OAuth handshake. Sign in and approve.
4. The connector shows **Connected** with your tools listed.

**Claude.ai example:** Settings → Connectors → Add custom connector → paste the URL.

### B. Local development via ngrok (free)

Most AI clients can't reach `localhost` on your laptop. Use ngrok to expose it:

1. **Install ngrok** — https://ngrok.com/download (free tier is fine; sign up for a free account to get an auth token).
2. **Authenticate** (one-time):
   ```bash
   ngrok config add-authtoken <your-authtoken>
   ```
3. **Start the tunnel** in a new terminal while Fluxito is running:
   ```bash
   ngrok http 8000
   ```
   You'll see output like:
   ```
   Forwarding  https://abc123.ngrok-free.app -> http://localhost:8000
   ```
4. **Update Fluxito's `APP_BASE_URL`** to the ngrok URL so OAuth redirects work. In your `.env`:
   ```
   APP_BASE_URL=https://abc123.ngrok-free.app
   ```
   Then `docker compose restart app`.
5. **Re-register your OAuth app redirect URIs** — for any platform you've already configured, the redirect URIs Fluxito advertises will now use the ngrok hostname. Update the matching entries in each vendor's developer console.
6. **Add the MCP connector** in your AI client with `https://abc123.ngrok-free.app/mcp`.
7. **When you're done testing**, kill ngrok and revert `APP_BASE_URL`.

> **Heads-up:** ngrok's free tier hands out a new random subdomain every time you restart the tunnel. Keep it running while you test to avoid re-doing redirect-URI registration. A paid ngrok plan or a real domain gives you a stable URL.

---

## Self-hosting paths

| Path | When |
|---|---|
| **Docker Compose** (default) | Local dev, small VPS, single-team install |
| **Render** ([`render.yaml`](render.yaml)) | Hosted Postgres + Redis + app, one-click |
| **Railway** ([`railway.json`](railway.json)) | Same idea; you add Postgres + Redis from Railway's UI |
| **Your own infra** | Any Docker-friendly host. Bring your own Postgres 15 + Redis 7. |

### Production notes (Docker Compose / reverse proxy)

The default `docker-compose.yml` runs four services:

- `nginx` (port 8000) — reverse proxy + static files. **Critical:** `proxy_buffering off` for `/mcp` (required for chunked MCP responses).
- `app` — FastAPI + Gunicorn (4 workers by default).
- `db` — Postgres 15 (data in `postgres_data` volume).
- `redis` — Redis 7 with AOF (data in `redis_data` volume).

First-time setup:

```bash
cp .env.example .env
docker compose up -d
docker compose exec app alembic upgrade head
```

If you put any reverse proxy (nginx, Cloudflare, ALB, etc.) in front of the app container, you **must** disable buffering on the MCP path or streaming will stall:

```nginx
location /mcp {
    proxy_pass http://app:8001;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
}
```

**Render / Railway:** Both are supported via the included `render.yaml` and `railway.json`. Add the Postgres + Redis add-ons and run `alembic upgrade head` as a pre-deploy/start command.

**Scaling:** 4 Gunicorn workers by default. Rough guideline: `2 × vCPU + 1`. Each worker uses ~100-150 MB idle / 200-300 MB under load. Plan ≥1 GB RAM for a 4-worker deployment.

**Secret rotation warnings:**

- Rotating `APP_SECRET_KEY` logs everyone out.
- Rotating `TOKEN_ENCRYPTION_KEY` makes every stored OAuth token permanently unreadable unless you also re-encrypt the `oauth_connections` (and related credential) tables with the new key before deploying.

**Migrations:** Always run `alembic upgrade head` before (or atomically with) new code. Additive migrations are safe for rolling deploys. Destructive ones require a backup + careful sequencing.

---

## Backups

The only stateful service is **Postgres**. Standard `pg_dump` applies:

```bash
docker compose exec db pg_dump -U postgres fluxito > fluxito-$(date +%F).sql
```

Also back up `.env.local` — it holds the auto-generated `TOKEN_ENCRYPTION_KEY`. Lose that key, lose the ability to decrypt OAuth tokens stored in your DB.

For production, use your managed Postgres provider's automated snapshots and store `TOKEN_ENCRYPTION_KEY` in your secrets manager.

---

## Common issues

### MCP client gets 401 on a valid-looking token

The token was hashed against a different `APP_SECRET_KEY`, the MCP session expired, or it was revoked. Re-authorize the connector in Claude.ai (Settings → Connectors).

### `redirect_uri_mismatch` (Google, Meta, etc.)

The exact redirect URI you registered in the vendor's developer console does not match what Fluxito is advertising (`APP_BASE_URL` + callback path). Compare character-for-character — `http` vs `https` and trailing slashes are the usual culprits.

### "OAuth app not configured" for a platform

You have not yet saved credentials for that platform in **Settings → Integrations**. Fluxito only enables the Connect button after the corresponding client ID and secret are stored in the database.

### Platform connector shows "disconnected" after a deploy or restart

Almost always caused by rotating `TOKEN_ENCRYPTION_KEY` without re-encrypting the stored tokens in the database. The Fernet key must be stable for the life of the installation.

### Circuit breaker open for a tool / connection

Five consecutive failures on the same connection key. Check `/api/health` for the current breaker snapshot. Fix the root cause (bad token, quota, network), wait 60 s, or restart the worker process.

### Google token "expired or revoked"

User changed password, revoked app access in Google Account, or (very common) the refresh token expired because the Google OAuth app is still in "Testing" mode. Re-authorize via `/connect`.

### Alembic migration fails

Usually a foreign-key or enum value issue on existing rows. The migrations are written to be as safe as possible. Run `alembic current` and `alembic history --verbose`, then open an issue with the exact error if you cannot resolve it.

### Docker Compose fails to start (port conflicts, etc.)

Local Postgres (5432) or Redis (6379) already running on the host. Either stop those services or change the published ports in `docker-compose.yml`. Also check that `.env` exists and `APP_SECRET_KEY` is ≥32 characters.

### `/setup` keeps appearing after a database wipe

The `users` table is empty. Create the first admin through the UI (no verification email is sent in self-hosted mode).

### General debugging commands

```bash
# Health (DB, Redis, circuit breakers, request stats)
curl http://localhost:8000/api/health | python -m json.tool

# Tail app logs
docker compose logs -f app

# Force migrations
docker compose exec app alembic upgrade head

# Current migration revision
docker compose exec app alembic current
```

For deeper token decryption (admin/debug only, never log the output):

```python
from cryptography.fernet import Fernet
from app.config import settings
f = Fernet(settings.TOKEN_ENCRYPTION_KEY.encode())
print(f.decrypt(b"...encrypted-bytes-from-db..."))
```

---

## Roadmap

Where we're going next:

- **Additional connectors** — Reddit Ads, Microsoft Advertising, Mixpanel, PostHog, Segment, Klaviyo, HubSpot, and more.
- **Advanced SDR capabilities** — generate tracking plans straight from the brief; auto-translate to GTM/GA4 implementation tasks; warn when implementation drifts from the plan; versioned diffing.
- **Tag audits** — automated end-to-end GTM container audits: broken tags, orphan triggers, missing conversions, best-practice scoring.
- **Ask Fluxito** — a native AI harness built into the platform, so you can chat with your marketing data directly from the Fluxito UI without needing an external AI client.

The roadmap is shaped by community priority. Open an issue to vote / request.

---

## Security

Found a vulnerability? Don't open a public GitHub issue — use [GitHub Security Advisories](https://github.com/digitalXperiments/fluxito/security/advisories/new) for private disclosure. See [SECURITY.md](SECURITY.md) for the full policy.

---

## Contributing

PRs welcome. The bar is in [CONTRIBUTING.md](CONTRIBUTING.md). Before you spend significant time, open an issue to discuss the approach — saves you reworking it after review.

---

## License

[Apache License 2.0](LICENSE). Copyright © 2026 Fluxito contributors. See [NOTICE](NOTICE) for third-party attributions.

Brand and platform names referenced (Google Analytics, Meta, TikTok, Snowflake, Adobe, etc.) are trademarks of their respective owners. Fluxito is not affiliated with or endorsed by any of these vendors.
