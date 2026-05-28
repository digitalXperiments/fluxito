# Fluxito Demo Deployment (Mac + Cloudflare Tunnel)

This folder contains everything you need to run the public Fluxito demo on your M1 MacBook Pro behind your existing Cloudflare Tunnel + Hermes supervisor.

All demo deployment artifacts live in this single top-level `demo/` folder for cleanliness.

## Files in this folder

- `docker-compose.demo.yml` — The demo stack (nginx + app + Postgres + Redis). Uses a published GHCR image, not a local build.
- `.env.demo.example` — Template for your secrets and demo settings. Copy to `.env.demo`.
- `update-demo.sh` — Pulls the latest image and restarts the stack with health verification. Safe to run from cron.
- `nginx.conf` — (You must copy this from the repo root when setting up.)

## One-time setup on your Mac

1. Create the demo directory:
   ```bash
   mkdir -p ~/fluxito-demo
   cd ~/fluxito-demo
   ```

2. Copy the files from this folder (or clone the repo and copy `demo/*`).

3. Copy the nginx config (required):
   ```bash
   cp /path/to/your/fluxito/nginx.conf ~/fluxito-demo/nginx.conf
   ```

4. Create your env file:
   ```bash
   cp .env.demo.example .env.demo
   ```

5. Edit `.env.demo`:
   - Generate `APP_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` (see comments in the file).
   - Set `DEMO_VIEWER_EMAIL=demo@fluxito.local` (or whatever email you choose for the public login).
   - Set `APP_BASE_URL=https://demo.yourdomain.com` (your public demo hostname).
   - Adjust `POSTGRES_PASSWORD` if you want something stronger.

6. Start the stack for the first time:
   ```bash
   docker compose -f docker-compose.demo.yml up -d
   ```

7. Do the initial admin setup (this is you, the real owner):
   - Visit `https://demo.yourdomain.com`
   - Go through `/setup` and create **your** real admin account (not the demo one).
   - Log in as yourself.
   - Create the public demo user (`demo@fluxito.local`) with a known password.
   - Make that user an `owner` of a new project called "Fluxito Demo".
   - Connect real platforms (Google, etc.) while logged in as yourself.
   - Build nice dashboards, SDRs, add audit history, etc. This becomes the public experience.

8. Add the Cloudflare Tunnel ingress rule (in your `cloudflared` config):
   ```yaml
   ingress:
     - hostname: demo.fluxito.yourdomain.com
       service: http://localhost:8010
     # ... your other rules
   ```
   Reload `cloudflared`.

9. Add the service to Hermes (or your supervisor of choice) so it starts on boot.

## Daily / CI-driven updates

The demo is updated by pulling a new Docker image built by GitHub Actions (see `.github/workflows/ci.yml` for the publish job).

Run the updater manually after a merge:
```bash
cd ~/fluxito-demo
./update-demo.sh
```

Or install a cron (recommended every 15–30 minutes):
```bash
crontab -e
# Add:
*/20 * * * * cd ~/fluxito-demo && ./update-demo.sh >> ~/fluxito-demo/update.log 2>&1
```

The script:
- Pulls the latest `:demo` tag (or whatever `FLUXITO_IMAGE` you set)
- Restarts the stack cleanly
- Waits for `/api/health` to return 200
- Exits non-zero and can notify you (via Telegram) on failure

You can also pin to a specific image for testing:
```bash
FLUXITO_IMAGE=ghcr.io/digitalxperiments/fluxito:sha-abc1234 ./update-demo.sh
```

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
