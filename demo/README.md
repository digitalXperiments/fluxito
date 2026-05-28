# Fluxito Demo Deployment (Mac + Cloudflare Tunnel)

This folder contains everything you need to run the public Fluxito demo on your M1 MacBook Pro behind your existing Cloudflare Tunnel + Hermes supervisor.

All demo deployment artifacts live in this single top-level `demo/` folder for cleanliness.

## Files in this folder

- `docker-compose.demo.yml` — The demo stack (nginx + app + Postgres + Redis). Uses a published GHCR image, not a local build.
- `.env.demo.example` — Template for your secrets and demo settings. Copy to `.env.demo`.
- `update-demo.sh` — Pulls the latest image and restarts the stack with health verification. Safe to run from cron.
- `nginx.conf` — Demo reverse-proxy config. Ships in this folder — use it as-is, do NOT copy the repo-root `nginx.conf`. It serves `/static/` directly off the `static_assets` volume, which the `static-sync` service populates from the published image (no source checkout needed on the host).

## One-time setup on your Mac

1. Create the demo directory:
   ```bash
   mkdir -p ~/fluxito-demo
   cd ~/fluxito-demo
   ```

2. Copy the files from this folder (or clone the repo and copy `demo/*`).
   The folder is self-contained — it ships its own `nginx.conf`. Do NOT copy
   the repo-root `nginx.conf`; static assets are served from a volume the
   stack populates from the published image, so no source checkout is needed.

3. Create your env file:
   ```bash
   cp .env.demo.example .env.demo
   ```

4. Edit `.env.demo`:
   - Generate `APP_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` (see comments in the file).
   - Set `DEMO_VIEWER_EMAIL=demo@fluxito.local` (or whatever email you choose for the public login).
   - Set `APP_BASE_URL=https://demo.yourdomain.com` (your public demo hostname).
   - Adjust `POSTGRES_PASSWORD` if you want something stronger.

5. Start the stack for the first time:
   ```bash
   docker compose -f docker-compose.demo.yml up -d
   ```

6. Do the initial admin setup (this is you, the real owner):
   - Visit `https://demo.yourdomain.com`
   - Go through `/setup` and create **your** real admin account (not the demo one).
   - Log in as yourself.
   - Create the public demo user (`demo@fluxito.local`) with a known password.
   - Make that user an `owner` of a new project called "Fluxito Demo".
   - Connect real platforms (Google, etc.) while logged in as yourself.
   - Build nice dashboards, SDRs, add audit history, etc. This becomes the public experience.

7. Add the Cloudflare Tunnel ingress rule (in your `cloudflared` config):
   ```yaml
   ingress:
     - hostname: demo.fluxito.yourdomain.com
       service: http://localhost:8010
     # ... your other rules
   ```
   Reload `cloudflared`.

8. Add the service to Hermes (or your supervisor of choice) so it starts on boot.

## Daily / CI-driven updates

**Important policy change (May 2026):**  
The `:demo` tag (the one your `update-demo.sh` pulls) is **only updated on stable releases**, not on every merge to `main`.

This keeps the public demo reliable and prevents WIP code from reaching visitors.

### When the `:demo` tag gets updated (stable triggers only)

- Git tags starting with `v` or `demo/stable-` (example: `git tag demo/stable-2026-05-28 && git push --tags`)
- Manual trigger in GitHub UI (Actions → "Publish stable Demo image" → Run workflow)
- Optional nightly schedule (03:00 UTC) — can be disabled in the workflow file

Every normal push to `main` still produces fast `sha-xxx` tags (for debugging/pinning), but those do **not** move the public `:demo` tag.

### How to trigger a new public demo image

**Option A — Git tag (recommended when you certify it's stable)**
```bash
git tag demo/stable-2026-05-28
git push origin demo/stable-2026-05-28
```

**Option B — GitHub UI (no git needed)**
1. Go to your repo → **Actions** tab.
2. In the left sidebar click **"Publish stable Demo image"**.
3. Click **"Run workflow"**.
4. (Optional) Fill in the `demo_tag` field with a custom value (e.g. `demo/stable-may-28`).
5. Click **"Run workflow"**.

The job will build the full multi-arch image and push `:demo` (plus `:latest` and your chosen tag).

**Option C — Nightly (set and forget)**
The workflow has a scheduled run at 03:00 UTC. It will only produce a new `:demo` image if the schedule is left enabled.

### Running the updater on your Mac

```bash
cd ~/fluxito-demo
./update-demo.sh
```

Or via cron (example: every 20 minutes):
```bash
crontab -e
# Add this line:
*/20 * * * * cd ~/fluxito-demo && ./update-demo.sh >> ~/fluxito-demo/update.log 2>&1
```

The script pulls the latest `:demo` image (whatever the stable pipeline last published), restarts the stack, and waits for `/api/health`.

You can also force a specific image:
```bash
FLUXITO_IMAGE=ghcr.io/digitalxperiments/fluxito:sha-abc1234 ./update-demo.sh
```

The script exits non-zero on failure and can send a Telegram notification if you set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the cron environment.

## Important notes

- The public demo login (`demo@fluxito.local`) can see **everything** as an admin except the MCP / AI connection endpoint.
- You (real admin login) have full power on the same instance, including MCP.
- All data lives in the `demo_postgres_data` and `demo_redis_data` Docker volumes. Image updates do **not** touch your data.
- If the demo ever gets messy, just log in as yourself and clean it up, or trigger a reset (if you added the optional reset endpoint).

## Power / sleep settings for 24/7 on M1 (lid closed)

To keep the demo reachable even when your Mac lid is closed:

```bash
# Prevent sleep while on AC power
sudo pmset -c sleep 0
sudo pmset -c hibernatemode 0
sudo pmset -c autopoweroff 0
sudo pmset -c powernap 0

# Keep Docker Desktop running in background
# Docker Desktop → Settings → General → "Start Docker Desktop when you log in" + "Always run in background"

# Optional: caffeinate wrapper (launchd plist or just leave a terminal with caffeinate -s)
```

Cloudflare Tunnel (`cloudflared`) should be configured as a user agent or launchd service so it survives sleep/wake.

## Troubleshooting

- Health failing after update → `docker compose -f docker-compose.demo.yml logs --tail=200 app`
- Can't reach the site → check Cloudflare Tunnel dashboard + `cloudflared` logs.
- Demo viewer can't log in → make sure you created the user while logged in as a real admin, and that the password is set.
- MCP still works for demo user → double-check that `DEMO_VIEWER_EMAIL` in `.env.demo` exactly matches the email you created.

## Future improvements (optional)

- Add a protected `/api/demo/reset` endpoint (secret header) + call it from a nightly cron or button only you know.
- Light web guards so the demo viewer can't complete real OAuth connects or publish GTM containers.
- Switch to a cheap always-on mini-PC or VPS later (same Docker image + compose file, just move the volumes).

This setup gives you a beautiful, always-up-to-date public demo with almost zero ongoing work after the initial curation.
