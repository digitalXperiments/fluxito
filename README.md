# Fluxito

**Open-source AI operations layer for marketing analytics. End-to-end — from tracking plan, to tag management implementation, to reporting and dashboards — through a conversation with any MCP-compatible AI.**

[![Version: 1.0.2](https://img.shields.io/badge/version-1.0.2-green.svg)](CHANGELOG.md)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.6-009688.svg)](requirements.txt)
[![MCP](https://img.shields.io/badge/MCP-compatible-6f42c1.svg)](https://modelcontextprotocol.io)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg)](Dockerfile)
[![Deploy: self-hosted](https://img.shields.io/badge/deploy-self--hosted-0f766e.svg)](docker-compose.yml)
[![Platforms: 15](https://img.shields.io/badge/platforms-15-orange.svg)](#connecting-your-platforms)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](pyproject.toml)
[![Code style: Ruff](https://img.shields.io/badge/code_style-Ruff-D7FF64.svg)](pyproject.toml)

---

## Why Fluxito

Running marketing analytics today means four separate jobs, usually owned by four different people or vendors:

1. **Define** what to track — events, parameters, user properties, conversions. Lives in a "tracking plan" Google Sheet that nobody updates.
2. **Implement** it — manually clicking through GTM, GA4, Meta Pixel, configuring tags / triggers / variables.
3. **Pipe** the data — warehouse syncs, custom dimensions, Looker connectors.
4. **Report** on it — build dashboards, run audits, surface anomalies, write the weekly recap deck.

Each step is slow, error-prone, and out of sync with the others. Tracking plans go stale. Implementations drift. Dashboards lie.

Fluxito collapses all four into a single conversation with your AI. **The AI is the operator; Fluxito gives it the hands.** It speaks the [Model Context Protocol](https://modelcontextprotocol.io) — an open standard — so it works with Claude, GPT, Gemini, Cursor, Windsurf, or any other MCP-compatible client.

Right now it gives your AI real, authenticated access across **15 platforms**:

| Stage | Platforms |
|---|---|
| **Define / SDR** | Generate, refine, and version Smart Data References (tracking plans + data dictionary) from live data + templates. Export to Excel. |
| **Implement** | Google Tag Manager (full create/update/delete of tags, triggers, variables with workspace support). Adobe Launch (read + some write). |
| **Measure** | Google Analytics 4, Adobe Analytics, Amplitude, Search Console |
| **Acquire** | Google Ads, Meta Ads, TikTok Ads, Snap Ads, LinkedIn Ads, Pinterest Ads |
| **Warehouse** | BigQuery, Snowflake, Redshift (query + some transformation) |
| **Report** | Build native dashboards (JSON cards, filterable, signed public links), scheduled email/Slack reports, automations |

You self-host it with Docker. All tokens are encrypted at rest. Nothing leaves your infrastructure except to the platforms you explicitly authorize.

---

## What you can actually do with it today

These are real prompts that work right now once Fluxito is connected and the AI has the right accounts linked. The AI calls the actual tools, not just describes what you should do.

> "Generate a tracking plan for our ecommerce checkout flow. Use what's live in GA4 and GTM right now, mix it with a solid industry template, and save it as our SDR v1."

> "Audit my GTM container GTM-XXXXXXX. Find broken tags, tags firing on the wrong triggers, and any GA4 events that have no conversion mapping."

> "Build a clean weekly dashboard with checkout funnel, paid ROAS, and SEO performance. Make the date filter work and give me a shareable public link."

> "My Google Ads conversions are way lower than GA4. Help me investigate using both data sources and tell me the most likely reasons for the gap."

> "Every Monday at 9am, send me a Slack message with the biggest anomalies across GA4, Meta Ads, and Google Ads from the previous week."

You're describing the outcome. The AI decides which tools to call, in what order, and against which connections. It handles the authentication and execution for you.

---

## Live Demo

You can try Fluxito right now with a real admin-like experience:

**URL:** https://fluxito-demo.insaneanalytics.co.in  
**Public login:** `demo@fluxito.local` / (password published on the demo page or ask the maintainer)

The demo runs the latest `main` build on a static curated dataset. You can browse:
- Dashboards and cards with real platform data
- SDRs with version history
- Audit logs and automation runs
- Settings, Integrations, and Connect pages (full admin UI chrome)

**Important restriction:** The public demo login cannot connect an AI or use the MCP endpoint (tool calling is disabled for security and cost reasons).  
When logged in as the real maintainer account on the same instance, full MCP + platform writes work normally.

To use the complete product (including connecting your own AI via MCP), [self-host Fluxito](#how-to-start-recommended-first-path).

---

## How to Start (Recommended First Path)

This is the shortest, highest-leverage way to get Fluxito running and connected to an AI.

**Honest time estimate the first time:**
- 3–5 minutes to get the server running locally
- 15–25 minutes for the Google OAuth app setup (one-time pain, unlocks five platforms)
- 5 minutes to connect your account + AI

After the first run it becomes much faster.

### What you need before you begin
- Docker + Docker Compose (Docker Desktop on Mac/Windows works great)
- A Google account that has access to at least one GA4 property or GTM container (strongly recommended for your first try — one OAuth app gives you GA4, GTM, Google Ads, Search Console, and BigQuery)

### 0. Create your .env file (exact commands — do this first)

This is the only file you need to create by hand. After the first admin account exists, you will almost never touch this file again.

Run these commands one by one in your terminal:

```bash
cd fluxito
cp .env.example .env
```

Now generate the two required secret keys. Copy and paste each command exactly:

**Command 1 – APP_SECRET_KEY** (this signs your login cookies):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the long string that appears and paste it after `APP_SECRET_KEY=` in the `.env` file.

**Command 2 – TOKEN_ENCRYPTION_KEY** (this encrypts all your platform tokens in the database):

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy that output and paste it after `TOKEN_ENCRYPTION_KEY=` in the same `.env` file.

Your `.env` file should now look like this (your actual keys will be different):

```
APP_SECRET_KEY=your-long-random-string-here
TOKEN_ENCRYPTION_KEY=your-fernet-key-here
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fluxito
REDIS_URL=redis://localhost:6379/0
APP_BASE_URL=http://localhost:8000
```

Save the file.

**Please keep a safe backup of this `.env` file.** If you ever lose the `TOKEN_ENCRYPTION_KEY` and don't have a backup, any tokens stored in the database will become unreadable. For your first local test this is usually fine — just don't delete the file.

That's all the `.env` work you need to do for now.

### 1. Clone and start the server

```bash
git clone https://github.com/digitalXperiments/fluxito.git
cd fluxito
docker compose up -d
```

This starts everything (Postgres, Redis, the app, and a reverse proxy on port 8000). Migrations run automatically. You don't need to touch anything else yet.

### 2. Open http://localhost:8000

You'll land on the setup screen because no admin exists yet.

Create the first admin with an email and password. No email verification is sent — you're running this yourself.

After signup you go straight to **Settings → Integrations**.

### 3. Set up Google (the single best first move)

This is the highest-ROI step. One Google OAuth app unlocks five platforms at once.

Follow the clear, step-by-step guide here:

→ **[Google Cloud Setup Guide](docs/tutorials/google-cloud-setup.md)** (15–20 min the first time)

It walks you through creating the project, enabling the right APIs, setting up the OAuth consent screen, and creating the OAuth client. When you're done, paste the Client ID + Secret back into Fluxito.

Once saved, the entire Google family lights up in the UI.

### 4. Connect your real Google account

Go to **/connect** and click the big **Connect** button for Google. Authorize Fluxito against the actual accounts you want the AI to work with (GA4 properties, GTM containers, Google Ads accounts, etc.).

### 5. Connect your AI (the fun part)

Fluxito is an MCP server. Any MCP-compatible client (Claude, GPT, Gemini, Cursor, Windsurf, etc.) can talk to it.

The easiest first experience for most people is testing locally with ngrok (free). Full instructions, including the exact ngrok steps and how to add the connector in Claude, are in the section below called **Connecting an AI client**.

Once connected, your AI will see all the tools Fluxito exposes.

### First things worth trying

Once your AI is connected and you have at least Google linked, these prompts actually work today:

> "Generate a tracking plan for our checkout flow. Use what's currently implemented in GA4 and GTM, merge it with a good ecommerce template, and save it as our first SDR."

> "Audit my GTM container GTM-XXXXXXX. Flag broken tags, tags firing on the wrong triggers, and any GA4 events that aren't mapped as conversions."

> "Build a simple weekly dashboard with sessions, key conversions, and channel performance. Make the date range filterable and give me a public link."

> "My Google Ads conversions don't match GA4. Help me investigate using both data sources and tell me where the gap is."

You're not asking the AI to write queries or click buttons for you. You're describing the outcome you want. It figures out which tools to call and in what order.

---

## Connecting your platforms

Each tutorial is a clear, marketer-friendly walkthrough for registering the OAuth app and pasting the credentials into Fluxito.

**Strongly recommended first path:** Start with Google. One OAuth client unlocks GA4, GTM, Google Ads, Search Console, and BigQuery.

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

All credentials are stored in the database and encrypted at rest. After the first admin exists, everything is configured in the web UI under **Settings → Integrations**. No `.env` files for platform credentials.

---

## Configuration

Fluxito has two distinct configuration surfaces:

1. **Environment variables** — loaded at startup by `app/config.py`. These are the only values the app needs before it can boot.
2. **Web UI** (`/settings/integrations` and `/settings/system` after the first admin signs in) — the normal place to configure OAuth apps and runtime settings.

### What actually goes in .env (bootstrap only)

You can mostly ignore this section after you finish the first run. The exact commands you need are already written out in the "How to Start" section above.

The `.env` file is now only for the five values Fluxito needs before the database is even available or before anyone has logged in. After the first admin exists, almost everything else (Google OAuth apps, other platform credentials, MCP redirect settings, email, rate limits, etc.) moves to the web UI.

Here they are in the simplest possible language:

- `APP_SECRET_KEY` — signs your login cookies. If you change it later, everyone gets logged out.
- `TOKEN_ENCRYPTION_KEY` — encrypts all the tokens (Google, Meta, etc.) that live in the database. If you lose this key without a backup, those tokens become permanently unreadable.
- `DATABASE_URL` — address of your Postgres database.
- `REDIS_URL` — address of your Redis instance.
- `APP_BASE_URL` — the public URL people (and your AI) use to reach Fluxito.

That's it. Five lines. The two secret keys are the only ones you generate yourself, and the commands to create them are already shown earlier in this guide.

Everything else is now done through the browser after the first admin signs in. The old way of putting platform credentials in `.env` files is gone.

### Platform OAuth apps (Meta, TikTok, LinkedIn, Pinterest, Snap, Adobe, etc.)

After the first admin exists, **everything** here happens in the web UI under **Settings → Integrations**.

The pattern is always the same:

1. Go to that platform's developer console and create an OAuth app.
2. Copy the Client ID and Client Secret.
3. Paste them into the matching card in Fluxito's Settings → Integrations.

Once saved, the "Connect" button for that platform becomes active at `/connect`.

If nothing is saved for a platform, the Connect button stays disabled.

This is the only way it works now. No more `.env` files for platform credentials.

---

## Connecting an AI client

Fluxito is an MCP server. You connect it directly inside Claude, ChatGPT, Cursor, Gemini, Windsurf, or any other tool that supports the [Model Context Protocol](https://modelcontextprotocol.io).

The endpoint is `/mcp`. Once added as a custom connector, your AI can see and call real tools against your platforms (with proper auth and audit trails).

**Note on Claude:** A lot of professionals use Claude right now, and it's currently one of the strongest models at tool calling and working with MCP servers. If you use Claude for work, this integration tends to feel especially natural.

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

## Roadmap & Honest State of Things

This is the first real step toward something much bigger. What exists today is already useful (you can generate SDRs, do real GTM work, build dashboards, run cross-platform analysis, etc.), but there is a lot still missing or rough.

Some things I want to get to:
- More connectors (Reddit Ads, Microsoft Advertising, Mixpanel, Klaviyo, etc.)
- Much stronger automated tag auditing and drift detection between plan and implementation
- A native "Ask Fluxito" chat experience inside the UI so you don't always need an external AI client
- Better versioning and diffing for SDRs
- More write actions on the advertising platforms

The roadmap will be heavily shaped by what actual users need. If something feels painful or missing, open an issue. That's the whole point of open sourcing it.

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
