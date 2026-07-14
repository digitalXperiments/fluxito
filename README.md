# Fluxito

### The AI operating system for marketing analytics.

**Plan → Implement → Audit → Report — one conversation, 27 platforms, zero spreadsheets.**

<p align="center">
  <a href="https://fluxito.app"><img src="https://img.shields.io/badge/-Try%20Fluxito%20free-2F5BF4?style=for-the-badge" alt="Try Fluxito"></a>
  <a href="#install"><img src="https://img.shields.io/badge/-Self--host%20with%20Docker-0f766e?style=for-the-badge&logo=docker&logoColor=white" alt="Self-host with Docker"></a>
  <a href="docs/tutorials/connect-ai-mcp.md"><img src="https://img.shields.io/badge/-Connect%20your%20AI-6f42c1?style=for-the-badge" alt="Connect your AI"></a>
</p>

<p align="center">
  <a href="https://github.com/digitalXperiments/fluxito/releases/latest"><img src="https://img.shields.io/github/v/release/digitalXperiments/fluxito?label=release&color=2F5BF4" alt="Release"></a>
  <a href="#connecting-your-platforms"><img src="https://img.shields.io/badge/platforms-27-orange.svg" alt="27 platforms"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue.svg" alt="Apache 2.0"></a>
</p>

---

## The loop that's broken

Marketing analytics is four jobs, usually four different owners, permanently out of sync:

| | | |
|---|---|---|
| 🗺️ | **Plan** | What to track — buried in a spreadsheet nobody opens twice |
| 🔧 | **Implement** | GTM, GA4, pixels — clicked in by hand, one tag at a time |
| 🔍 | **Audit** | Does it actually fire? Usually nobody checks until revenue looks wrong |
| 📊 | **Report** | Dashboards built on data nobody verified |

Plans go stale. Tags break silently. Dashboards lie with confidence. Every handoff between these four jobs is where the truth leaks out.

**Fluxito closes the loop.** It's one system that plans, implements, audits, and reports — driven entirely by your AI. You describe the outcome; Fluxito gives your AI real, authenticated hands across the whole stack via the [Model Context Protocol](https://modelcontextprotocol.io) — Claude, ChatGPT, Cursor, Windsurf, or anything else that speaks MCP.

---

## Plan → Implement → Audit → Report

**Plan** — Your AI reads what's already live in GA4/GTM, merges it with proven templates, and produces a versioned tracking plan (SDR) — not a spreadsheet that rots.

> "Generate a tracking plan for our checkout flow from what's live in GA4 and GTM, merged with a solid ecommerce template. Save it as SDR v1."

**Implement** — The AI ships the plan directly: tags, triggers, variables, workspaces in GTM, no copy-paste between a doc and a console.

> "Implement SDR v1 in GTM-XXXXXXX. Create the tags and triggers, publish to a new workspace."

**Audit** — Fluxito checks what's *actually* firing against 25+ platform rule books, and flags drift between the plan and reality before it costs you data.

> "Audit container GTM-XXXXXXX. Flag broken tags, wrong triggers, and GA4 events with no conversion mapping."

**Report** — Native dashboards with 19 chart types, built card-by-card in conversation: Ask Fluxito proposes a chart, shows you a live preview, and adds it only when you click **Add**. Plus scheduled Slack/email recaps and root-cause investigation across every connected platform.

> "Build me a funnel from sessions → add-to-cart → checkout → purchase for the last 30 days, and add it to the Growth dashboard."
>
> "Every Monday at 9am, Slack me the biggest anomalies across GA4, Meta Ads, and Google Ads."
>
> "Google Ads conversions lag GA4 — investigate both sources and explain the gap."

You describe outcomes. The AI picks the tools, the order, and the connections. Fluxito handles auth, permissions, and execution.

---

## Already shipped

Not a wishlist — this is live in the product today:

- ✅ **Tracking-plan reconciliation** — diff live GA4/GTM events against a saved SDR (new / updated / unchanged / conflicts), on demand from a conversation
- ✅ **Live tag audits** against 25+ platform rule books — broken tags, wrong triggers, unmapped conversions
- ✅ **Campaign write operations across all 9 ad platforms** — Google, Meta, TikTok, Snap, LinkedIn, Pinterest, X, Reddit, Apple Search Ads (create campaigns, update budget/status)
- ✅ **Native dashboards, 19 chart types** — funnels, heatmaps, treemaps, gauges, waterfalls, combos and more, with filters, shareable public links, and scheduled PDF/email/Slack delivery
- ✅ **Chat-based dashboard builder** — describe the chart, preview it live in Ask Fluxito, click **Add**; nothing is written without your confirmation
- ✅ **Ask Fluxito** — a built-in assistant on top of Claude, GPT, Gemini, xAI, Mistral, or a local model
- ✅ **MCP + Fluxito Skills** — any MCP-compatible AI client gets full tool access, not just the hosted assistant
- ✅ **Project-scoped RBAC** — per-tool, per-connection roles shared by the API and the UI

---

## 27 platforms, one connection layer

| Stage | Platforms |
|---|---|
| **Plan** | Tracking plans + data dictionary from live data & templates; versioned; Excel export |
| **Implement** | Google Tag Manager (full tag/trigger/variable CRUD + workspaces); Adobe Launch |
| **Audit / Measure** | GA4, Adobe Analytics, Amplitude, Mixpanel, PostHog, Adjust, AppsFlyer, Branch, Search Console, Bing Webmaster |
| **Acquire** | Google Ads, Meta Ads, TikTok Ads, Snap Ads, LinkedIn Ads, Pinterest Ads, X Ads, Reddit Ads, Apple Search Ads |
| **Nurture** | Adobe Marketo Engage, Braze, MoEngage |
| **Warehouse** | BigQuery, Snowflake, Redshift |
| **Report** | Native dashboards (19 chart types, chat builder, filters, public links, PDF), scheduled email/Slack, automations, activity log |

Setup walkthroughs for each platform live under [`docs/tutorials/`](docs/tutorials/).

---

## Hosted, or your own infra

**[fluxito.app](https://fluxito.app)** is the full product, not a trial: tracking plans, GTM work, live tag audits, native dashboards, scheduled automations, team RBAC, and **Ask Fluxito** — a built-in assistant that runs on Claude, GPT, Gemini, xAI, Mistral, or a local model via LM Studio.

Prefer your own infra? Everything below is the same product, self-hosted with Docker — encrypted credentials at rest, project-scoped RBAC, nothing leaves your infrastructure except to platforms you explicitly authorize.

---

## Install

### Docker (recommended — no clone)

```bash
mkdir fluxito && cd fluxito
curl -O https://raw.githubusercontent.com/digitalXperiments/fluxito/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/digitalXperiments/fluxito/main/.env.example

# One-click in-app updates (optional but recommended)
echo "UPDATER_TOKEN=$(openssl rand -hex 32)" >> .env

# Pin secrets so encrypted tokens survive restarts/updates
docker pull ghcr.io/digitalxperiments/fluxito:latest
echo "APP_SECRET_KEY=$(docker run --rm ghcr.io/digitalxperiments/fluxito python -c 'import secrets; print(secrets.token_hex(32))')" >> .env
echo "TOKEN_ENCRYPTION_KEY=$(docker run --rm ghcr.io/digitalxperiments/fluxito python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" >> .env

docker compose up -d
```

Open **http://localhost:8000**. Update anytime from **Admin → Updates**, or:

```bash
docker compose pull && docker compose up -d
```

### From source (track `main` / fork)

```bash
git clone https://github.com/digitalXperiments/fluxito.git
cd fluxito
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Optional in `.env` so you don't repeat both compose files:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml
```

---

## Updating

**Release images:** super-admin **Admin → Updates → Update now** (pulls image, recreates container, rolls back on failed health). Needs `UPDATER_TOKEN` in `.env`. Without it, use `docker compose pull && docker compose up -d`.

**Source installs:**

```bash
git pull
docker compose up -d --build   # or with both compose files if COMPOSE_FILE isn't set
```

Migrations run on startup (`alembic upgrade head`). Named volumes for Postgres/Redis persist — **never** `docker compose down -v` to update.

**Air-gapped:** set `UPDATE_CHECKS_ENABLED=false` to stop outbound GitHub version checks.

---

## First run

Rough first-time cost: **15–25 min** for Google OAuth (one app unlocks five platforms), then **~5 min** to connect accounts + AI.

1. Open http://localhost:8000 → create the first admin (email + password; no email verification on self-host).
2. **Settings → Integrations** → follow **[Google Cloud Setup](docs/tutorials/google-cloud-setup.md)** (GA4, GTM, Ads, Search Console, BigQuery).
3. **/connect** → authorize the Google accounts you want the AI to use.
4. Connect an MCP client (or use **Ask Fluxito** in the UI). See **[Connect AI with MCP](docs/tutorials/connect-ai-mcp.md)** and the section below.

---

## Connecting your platforms

Start with Google when you can — one OAuth client unlocks five surfaces.

| Platform | Tutorial |
|---|---|
| **Google** (GA4, GTM, Ads, Search Console, BigQuery) | [google-cloud-setup.md](docs/tutorials/google-cloud-setup.md) |
| Google Analytics 4 | [google-analytics-4.md](docs/tutorials/google-analytics-4.md) |
| Google Tag Manager | [google-tag-manager.md](docs/tutorials/google-tag-manager.md) |
| Google Ads | [google-ads.md](docs/tutorials/google-ads.md) |
| Search Console | [search-console.md](docs/tutorials/search-console.md) |
| Bing Webmaster Tools | [bing-webmaster.md](docs/tutorials/bing-webmaster.md) |
| BigQuery | [bigquery.md](docs/tutorials/bigquery.md) |
| Meta Ads | [meta-ads.md](docs/tutorials/meta-ads.md) |
| TikTok Ads | [tiktok-ads.md](docs/tutorials/tiktok-ads.md) |
| LinkedIn Ads | [linkedin-ads.md](docs/tutorials/linkedin-ads.md) |
| Pinterest Ads | [pinterest-ads.md](docs/tutorials/pinterest-ads.md) |
| Snap Ads | [snap-ads.md](docs/tutorials/snap-ads.md) |
| X Ads | [x-ads.md](docs/tutorials/x-ads.md) |
| Reddit Ads | [reddit-ads.md](docs/tutorials/reddit-ads.md) |
| Apple Search Ads | [apple-ads.md](docs/tutorials/apple-ads.md) |
| Snowflake | [snowflake.md](docs/tutorials/snowflake.md) |
| Redshift | [redshift.md](docs/tutorials/redshift.md) |
| Amplitude | [amplitude.md](docs/tutorials/amplitude.md) |
| Adobe Analytics | [adobe-analytics.md](docs/tutorials/adobe-analytics.md) |
| Adobe Launch | [adobe-launch.md](docs/tutorials/adobe-launch.md) |
| Adobe Marketo Engage | [adobe-marketo.md](docs/tutorials/adobe-marketo.md) |
| Mixpanel | [mixpanel.md](docs/tutorials/mixpanel.md) |
| PostHog | [posthog.md](docs/tutorials/posthog.md) |
| Adjust | [adjust.md](docs/tutorials/adjust.md) |
| AppsFlyer | [appsflyer.md](docs/tutorials/appsflyer.md) |
| Branch | [branch.md](docs/tutorials/branch.md) |
| Braze | [braze.md](docs/tutorials/braze.md) |
| MoEngage | [moengage.md](docs/tutorials/moengage.md) |

Credentials are encrypted at rest and managed in **Settings → Integrations** after the first admin exists — not via `.env`.

---

## Team access (RBAC)

Projects are multi-user. One role model covers **MCP tools and the web UI**.

| Tier | Access |
|---|---|
| **Owner** | Everything + structural actions (transfer ownership, delete project) |
| **Admin** | Full tools/connections; manage members and roles |
| **Member** | No access until assigned role(s) |

Custom roles (**Settings → User Roles**):

- **Tools by domain** — analytics, tag manager, marketing, SEO, warehouse, dashboards, knowledge, automation, analysis, tracking plan (read/write)
- **Connections by provider** — e.g. GA4 + Search Console, but not Ads

Members can hold multiple roles (union of permissions). Ungranted tools are hidden from `tools/list` and re-checked at execution. RBAC is **off by default** per project.

---

## Configuration

Two surfaces:

1. **`.env` / environment** — bootstrap only (before DB / first login)
2. **Web UI** — OAuth apps, system settings, MCP, email, rate limits, etc.

### Bootstrap env vars

| Variable | Purpose |
|---|---|
| `APP_SECRET_KEY` | Signs session cookies (≥32 chars). Rotating logs everyone out. |
| `TOKEN_ENCRYPTION_KEY` | Fernet key for OAuth/API credentials in DB. **Losing it orphans tokens.** |
| `DATABASE_URL` | Postgres |
| `REDIS_URL` | Redis |
| `APP_BASE_URL` | Public URL (OAuth redirects + MCP clients) |

Install commands above generate the two secrets. Platform OAuth apps are **only** configured in **Settings → Integrations** (paste client ID/secret → Connect lights up on `/connect`).

---

## Connecting an AI client (MCP)

Endpoint: **`/mcp`**. Full guide: **[docs/tutorials/connect-ai-mcp.md](docs/tutorials/connect-ai-mcp.md)**.
Optional: install **[Fluxito Skills](fluxito-skills/)** so agents use tools the intended way.

### Hosted / public URL

1. Add a custom MCP server in Claude, ChatGPT, Cursor, etc.
2. URL: `https://your-host/mcp`
3. Complete OAuth (or use a PAT for headless clients — see the tutorial)

### Local via ngrok

```bash
ngrok http 8000
# set APP_BASE_URL to the https://….ngrok-free.app URL, restart app
# add https://….ngrok-free.app/mcp as the MCP connector
```

Update vendor OAuth redirect URIs when the public hostname changes. Free ngrok subdomains rotate on restart — keep the tunnel up while testing, or use a stable domain.

---

## Self-hosting

| Path | When |
|---|---|
| **Docker Compose** | Default — local, VPS, single team |
| **[render.yaml](render.yaml)** | Render one-click style |
| **[railway.json](railway.json)** | Railway + add Postgres/Redis |
| **Your infra** | Any Docker host; Postgres 15 + Redis 7 |

Default stack: `nginx` (port 8000) → `app` (FastAPI/Gunicorn) + `db` + `redis` (+ optional `updater` sidecar).

**MCP reverse proxy must not buffer** `/mcp` (chunked streaming):

```nginx
location /mcp {
    proxy_pass http://app:8001;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    add_header X-Accel-Buffering no always;
}
```

**Scaling:** default 4 Gunicorn workers (~`2 × vCPU + 1`). Plan ≥1 GB RAM for four workers.

**Secrets:** rotating `TOKEN_ENCRYPTION_KEY` without re-encrypting DB credentials permanently breaks stored connections.

---

## Backups

Postgres is the source of truth:

```bash
docker compose exec db pg_dump -U postgres fluxito > fluxito-$(date +%F).sql
```

Also back up `.env` (especially `TOKEN_ENCRYPTION_KEY`). Managed Postgres snapshots + a secrets manager are recommended in production.

---

## Common issues

| Symptom | Likely fix |
|---|---|
| MCP 401 | Re-authorize connector; check `APP_SECRET_KEY` / session expiry |
| `redirect_uri_mismatch` | `APP_BASE_URL` + path must match vendor console exactly |
| "OAuth app not configured" | Save client ID/secret under **Settings → Integrations** |
| Disconnected after deploy | `TOKEN_ENCRYPTION_KEY` changed without re-encryption |
| Circuit breaker open | Fix root cause; check `/api/health`; wait ~60s |
| Google token revoked | Re-connect; Testing-mode apps expire refresh tokens quickly |
| Port conflict on compose | Host already using 5432/6379 — stop them or remap ports |

```bash
curl http://localhost:8000/api/health | python -m json.tool
docker compose logs -f app
docker compose exec app alembic upgrade head
docker compose exec app alembic current
```

---

## Roadmap

Useful today; still evolving. Direction of travel:

- More connectors (Microsoft Advertising, Klaviyo, HubSpot, Salesforce)
- Ad write paths beyond campaign-level — ad set and ad-level create/update
- Reverse-ETL — writing back to the warehouse, not just querying it
- Proactive, scheduled tracking-plan ↔ live-tag drift alerts (today: on-demand reconciliation)
- Richer Ask Fluxito (files, project memory, custom instructions)
- Dashboard drag-reorder & resize, chart images in Slack digests, branded scheduled PDFs
- Statistical anomaly detection with root-cause hints (today: biggest-movers ranking)

Shape it with issues and PRs — that's the point of open source.

---

## Security

Report vulnerabilities privately via [GitHub Security Advisories](https://github.com/digitalXperiments/fluxito/security/advisories/new). See [SECURITY.md](SECURITY.md).

---

## Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Open an issue before large work so scope stays aligned.

---

## License

[Apache License 2.0](LICENSE). Copyright © 2026 Fluxito contributors. See [NOTICE](NOTICE) for third-party attributions.

Brand and platform names (Google Analytics, Meta, TikTok, Snowflake, Adobe, etc.) are trademarks of their respective owners. Fluxito is not affiliated with or endorsed by those vendors.
