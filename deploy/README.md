# Fluxito Production Deployment (Mac + Cloudflare Tunnel)

This folder runs a self-hosted Fluxito production instance that **builds from
the local source checkout** — no container registry. The prod host holds a git
clone of this repo and tracks `main` as the stable channel.

## Files

- `docker-compose.prod.yml` — The stack (nginx + app + Postgres + Redis). The
  `app` image is **built from source** (`build: { context: .. }`); nginx serves
  `/static/` from the bind-mounted source tree.
- `.env.prod.example` — Template for secrets/config. Copy to `.env.prod`.
- `update.sh` — Run on the prod Mac to deploy: pulls `main`, rebuilds, restarts,
  health-checks.
- `nginx.conf` — Reverse-proxy config (serves `/static/` directly).

## One-time setup on the prod Mac

1. **Install Docker Desktop** and enable "Start on login" + "Always run in background".

2. **Clone the repo** (needs read access — use a deploy key or `gh auth login`):
   ```bash
   git clone git@github.com:digitalxperiments/fluxito.git ~/fluxito
   cd ~/fluxito
   ```

3. **Create the env file** (lives only here, never committed, survives every pull):
   ```bash
   cp deploy/.env.prod.example deploy/.env.prod
   # Edit deploy/.env.prod:
   #  - APP_SECRET_KEY        (python -c "import secrets; print(secrets.token_urlsafe(48))")
   #  - TOKEN_ENCRYPTION_KEY  (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
   #  - APP_BASE_URL          (your public https URL)
   #  - POSTGRES_PASSWORD     (something strong)
   #  - GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
   ```

4. **First build + start:**
   ```bash
   docker compose --env-file deploy/.env.prod -f deploy/docker-compose.prod.yml up -d --build
   ```

5. **Cloudflare Tunnel ingress** (in your `cloudflared` config):
   ```yaml
   ingress:
     - hostname: your-domain.example.com
       service: http://localhost:8010
     # ... your other rules
   ```
   Reload `cloudflared`.

6. **Create your admin account** at the public URL via `/setup`.

## Deploying a release

`main` is the stable channel. To ship the latest `main` to production, SSH into
the prod Mac and run the updater:

```bash
ssh <prod-mac>
cd ~/fluxito
./deploy/update.sh
```

`update.sh` does: `git pull --ff-only origin main` → rebuild + restart the stack
→ wait for `/api/health`. It exits non-zero on failure and can send a Telegram
alert if `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are set in the environment.

## Notes

- **Data** lives in the `fluxito_postgres_data` and `fluxito_redis_data` Docker
  volumes. Deploys rebuild the app image but never touch these — accounts and
  content survive every update.
- **`.env.prod`** is gitignored and never overwritten by a pull.
- This is a **fresh production instance** — it does not carry over data from the
  old demo stack (volumes were renamed).

## Power / sleep settings for 24/7 on a Mac (lid closed)

```bash
sudo pmset -c sleep 0
sudo pmset -c hibernatemode 0
sudo pmset -c autopoweroff 0
sudo pmset -c powernap 0
```

Keep Docker Desktop and `cloudflared` running as background/launchd agents so
they survive sleep/wake.

## Troubleshooting

- Health failing after deploy → `docker compose -f deploy/docker-compose.prod.yml logs --tail=200 app`
- Can't reach the site → check the Cloudflare Tunnel dashboard + `cloudflared` logs.
- `git pull --ff-only` fails → the prod checkout diverged from `main`; inspect
  with `git status` and reset to `origin/main` if safe.
