# Standalone Docker-Pull Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Fluxito installable with no `git clone` — a user downloads one `docker-compose.yml` (+ `.env`) and `docker compose up -d` pulls every service as a prebuilt GHCR image (app, updater, nginx), static included; build-from-source remains a secondary path.

**Architecture:** Publish three multi-arch images to GHCR (`fluxito`, `fluxito-updater`, `fluxito-nginx`) from the release pipeline. nginx serves `/static` by proxying to the app (static is baked into the app image and served by FastAPI) so no host files are needed, and its config is baked into the `fluxito-nginx` image. The default `docker-compose.yml` becomes mount-free (no repo source); a `docker-compose.build.yml` override rebuilds all three from source for developers. The release workflow auto-updates `CHANGELOG.md` and the README carries a dynamic version badge.

**Tech Stack:** Docker / Docker Compose, GitHub Actions (buildx, native amd64 + arm64 runners), GHCR, nginx, FastAPI.

**Context — current state (already shipped on `main`):**
- App image `ghcr.io/digitalxperiments/fluxito:${FLUXITO_VERSION:-latest}` is published and pulled by compose.
- `app/main.py:323` mounts `/static` via `StaticFiles`; `app/static` is baked into the image. `docs/tutorials/*.md` is also baked in (`.dockerignore` line 77 `!docs/tutorials/*.md`).
- `nginx.conf` serves `/static/` from a host mount (`alias /app/app/static/`) and proxies `/`, `/api/health`, `/mcp` (with `proxy_buffering off`) to `app:8001`.
- `docker-compose.yml`: `nginx` uses `nginx:alpine` + mounts `./nginx.conf` and `./app/static`; `app` mounts `./docs`; `updater` uses `build: ./updater`.
- `.github/workflows/release.yml`: `prepare` → `build` (matrix: amd64 on `ubuntu-latest`, arm64 on `ubuntu-24.04-arm`, push-by-digest, single app image) → `merge` (one manifest, tag, GitHub release with `--generate-notes`).
- `CHANGELOG.md` has a hand-written `## [Unreleased]` block (the self-update feature) that was never promoted to `## [1.0.4]`.

**GHCR image names (all lowercase):**
- app: `ghcr.io/digitalxperiments/fluxito`
- updater: `ghcr.io/digitalxperiments/fluxito-updater`
- nginx: `ghcr.io/digitalxperiments/fluxito-nginx`

---

## File Structure

**Created:**
- `Dockerfile.nginx` — builds `fluxito-nginx` (= `nginx:alpine` + baked `nginx.conf`).

**Modified:**
- `nginx.conf` — `/static/` proxies to the app instead of serving a host mount.
- `docker-compose.yml` — nginx → published image (no mounts), updater → published image, drop `./docs` and nginx host mounts; mount-free except the updater's docker socket + the user's own `.env`/compose + named volumes.
- `docker-compose.build.yml` — add `build:` for `app`, `updater`, `nginx`.
- `.github/workflows/release.yml` — build + push all 3 images (multi-arch) + 3 manifests; auto-update `CHANGELOG.md`.
- `CHANGELOG.md` — one-time: promote `[Unreleased]` → `[1.0.4]`.
- `README.md` — standalone pull as the primary install; build-from-source secondary; dynamic version badge.

---

### Task 1: nginx serves `/static` by proxying to the app

**Files:**
- Modify: `nginx.conf:46-52` (the `location /static/` block)

- [ ] **Step 1: Replace the static-file block with a proxy block**

In `nginx.conf`, replace:

```nginx
        # ── Static files — served directly by nginx, bypass FastAPI ────
        location /static/ {
            alias /app/app/static/;
            expires 30d;
            add_header Cache-Control "public, immutable";
            gzip_static on;
            access_log off;
        }
```

with:

```nginx
        # ── Static files — proxied to the app (static is baked into the app
        #    image and served by FastAPI). No host mount needed, and assets
        #    always match the running app version.
        location /static/ {
            proxy_pass         http://app;
            proxy_http_version 1.1;
            proxy_set_header   Host       $http_host;
            proxy_set_header   Connection "";
            expires 30d;
            add_header Cache-Control "public, immutable";
            access_log off;
        }
```

- [ ] **Step 2: Validate nginx config syntax in a throwaway container**

Run:
```bash
docker run --rm -v "$PWD/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:alpine nginx -t
```
Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`.
(If `docker` is unavailable, report DONE_WITH_CONCERNS — the build in Task 2 will also validate it.)

- [ ] **Step 3: Commit**

```bash
git add nginx.conf
git commit -m "feat(nginx): proxy /static to the app (no host mount)"
```

---

### Task 2: `Dockerfile.nginx` — the `fluxito-nginx` image

**Files:**
- Create: `Dockerfile.nginx`

- [ ] **Step 1: Create `Dockerfile.nginx`**

```dockerfile
# fluxito-nginx: nginx with the Fluxito reverse-proxy config baked in, so the
# standalone (no-clone) compose needs no host config file. Static is proxied to
# the app — nothing is served from a host mount.
FROM nginx:alpine

COPY nginx.conf /etc/nginx/nginx.conf

# Fail the build early if the baked config is invalid.
RUN nginx -t
```

- [ ] **Step 2: Build it (validates the baked config)**

Run:
```bash
docker build -f Dockerfile.nginx -t fluxito-nginx-test .
```
Expected: build succeeds; the `RUN nginx -t` layer prints `test is successful`.
(If `docker` is unavailable for environment reasons, complete the file + commit and report DONE_WITH_CONCERNS noting the build wasn't run.)

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.nginx
git commit -m "feat: fluxito-nginx image with baked reverse-proxy config"
```

---

### Task 3: Standalone (mount-free) `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml` (the `nginx`, `app`, `updater` services)

- [ ] **Step 1: Point `nginx` at the published image and drop its host mounts**

Replace the `nginx` service's image + volumes. Change:

```yaml
  nginx:
    container_name: fluxito-nginx
    image: nginx:alpine
    ports:
      - "8000:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./app/static:/app/app/static:ro        # Serve static files directly
    depends_on:
```

to:

```yaml
  nginx:
    container_name: fluxito-nginx
    image: ghcr.io/digitalxperiments/fluxito-nginx:${FLUXITO_VERSION:-latest}
    ports:
      - "8000:80"
    depends_on:
```

(Remove the entire `volumes:` block from `nginx` — config is baked into the image and static is proxied to the app. Keep `ports`, `depends_on`, `healthcheck`, `restart`.)

- [ ] **Step 2: Drop the `./docs` mount from `app`**

In the `app` service, remove the `volumes:` block entirely:

```yaml
    volumes:
      - ./docs:/app/docs:ro
```

`docs/tutorials/*.md` is baked into the app image, so this host mount would only shadow it with an empty dir in a no-clone install. (Leave the rest of `app` — `image`, `environment`, `env_file`, `depends_on`, `healthcheck`, `restart`, `deploy` — unchanged.)

- [ ] **Step 3: Point `updater` at the published image**

In the `updater` service, change:

```yaml
  updater:
    container_name: fluxito-updater
    build: ./updater
```

to:

```yaml
  updater:
    container_name: fluxito-updater
    image: ghcr.io/digitalxperiments/fluxito-updater:${FLUXITO_VERSION:-latest}
```

Keep the `updater` `environment:` and `volumes:` (docker socket, `./docker-compose.yml`, `./.env`, `update_state`) and `restart` exactly as they are — those mounts are the user's own downloaded files, not a repo clone.

- [ ] **Step 4: Validate the standalone compose**

Run:
```bash
UPDATER_TOKEN=devtoken docker compose -f docker-compose.yml config >/dev/null && echo "compose ok"
```
Expected: `compose ok`. Then confirm there are NO repo-source bind mounts left (only docker.sock, the user's compose/.env, and named volumes):
```bash
UPDATER_TOKEN=devtoken docker compose -f docker-compose.yml config | grep -nE "source:|\.\/|/app/app/static|nginx.conf|/app/docs" | head
```
Expected: the only host paths are `/var/run/docker.sock`, `./docker-compose.yml`, and `./.env` (under `updater`). No `./app/static`, no `./nginx.conf`, no `./docs`, no `build:` on nginx/app/updater. Paste the output.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: mount-free standalone compose (nginx + updater as GHCR images)"
```

---

### Task 4: `docker-compose.build.yml` — build all three from source

**Files:**
- Modify: `docker-compose.build.yml`

- [ ] **Step 1: Add build contexts for app, updater, nginx**

Replace the file contents with:

```yaml
# Developer override: build all images from local source instead of pulling the
# published GHCR images.
#   docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
name: fluxito

services:
  app:
    build:
      context: .

  updater:
    build:
      context: ./updater

  nginx:
    build:
      context: .
      dockerfile: Dockerfile.nginx
```

- [ ] **Step 2: Validate the merged build config**

Run:
```bash
UPDATER_TOKEN=devtoken docker compose -f docker-compose.yml -f docker-compose.build.yml config | grep -nE "build:|context:|dockerfile:" | head
```
Expected: `app` → context `.`; `updater` → context `./updater`; `nginx` → context `.` + dockerfile `Dockerfile.nginx`. Each service shows both an `image:` (from the base file) and a `build:` (from the override). Paste the output.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.build.yml
git commit -m "feat: build override builds app + updater + nginx from source"
```

---

### Task 5: Release workflow — build + push all three multi-arch images

**Files:**
- Modify: `.github/workflows/release.yml` (the `build` and `merge` jobs)

The current `build` job builds only the app image and `merge` makes one manifest. Extend both to cover all three images. Keep the existing `prepare` job and the `env: IMAGE: ghcr.io/digitalxperiments/fluxito` line; add two more image envs.

- [ ] **Step 1: Add image name envs**

At the top-level `env:` block, change:

```yaml
env:
  IMAGE: ghcr.io/digitalxperiments/fluxito
```

to:

```yaml
env:
  IMAGE_APP: ghcr.io/digitalxperiments/fluxito
  IMAGE_UPDATER: ghcr.io/digitalxperiments/fluxito-updater
  IMAGE_NGINX: ghcr.io/digitalxperiments/fluxito-nginx
```

- [ ] **Step 2: Replace the `build` job's build/export/upload steps**

Keep the `build` job's `needs`, `if`, `strategy` (matrix amd64/arm64 native runners), `runs-on`, and the Checkout / Set up Buildx / Log in to GHCR steps. Replace everything from the first "Build + push by digest" step onward with three build steps + a single digest export/upload that preserves per-image subdirectories:

```yaml
      - name: Build + push app (${{ matrix.arch }})
        id: build_app
        uses: docker/build-push-action@v6
        with:
          context: .
          platforms: ${{ matrix.platform }}
          build-args: |
            APP_VERSION=${{ needs.prepare.outputs.full }}
          labels: |
            org.opencontainers.image.version=${{ needs.prepare.outputs.full }}
            org.opencontainers.image.source=https://github.com/digitalXperiments/fluxito
          outputs: type=image,name=${{ env.IMAGE_APP }},push-by-digest=true,name-canonical=true,push=true

      - name: Build + push updater (${{ matrix.arch }})
        id: build_updater
        uses: docker/build-push-action@v6
        with:
          context: ./updater
          platforms: ${{ matrix.platform }}
          labels: |
            org.opencontainers.image.version=${{ needs.prepare.outputs.full }}
            org.opencontainers.image.source=https://github.com/digitalXperiments/fluxito
          outputs: type=image,name=${{ env.IMAGE_UPDATER }},push-by-digest=true,name-canonical=true,push=true

      - name: Build + push nginx (${{ matrix.arch }})
        id: build_nginx
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.nginx
          platforms: ${{ matrix.platform }}
          labels: |
            org.opencontainers.image.version=${{ needs.prepare.outputs.full }}
            org.opencontainers.image.source=https://github.com/digitalXperiments/fluxito
          outputs: type=image,name=${{ env.IMAGE_NGINX }},push-by-digest=true,name-canonical=true,push=true

      - name: Export digests
        run: |
          mkdir -p /tmp/digests/app /tmp/digests/updater /tmp/digests/nginx
          touch "/tmp/digests/app/${{ steps.build_app.outputs.digest }}"
          touch "/tmp/digests/updater/${{ steps.build_updater.outputs.digest }}"
          touch "/tmp/digests/nginx/${{ steps.build_nginx.outputs.digest }}"

      - name: Upload digests
        uses: actions/upload-artifact@v4
        with:
          name: digests-${{ matrix.arch }}
          path: /tmp/digests/*
          if-no-files-found: error
          retention-days: 1
```

> Note: `touch "/tmp/digests/app/sha256:abc…"` creates a file literally named `sha256:<hex>` inside the `app/` subdir. The merge job parses the basename back into a digest reference.

- [ ] **Step 3: Replace the `merge` job's manifest step**

Keep the `merge` job's `needs`, `if`, `runs-on`, Checkout, Download digests, Set up Buildx, and Log in to GHCR steps — but change the download to land both arches' trees together, and replace the single "Create + push multi-arch manifest" + "Inspect" steps with a loop over the three images. The Download step:

```yaml
      - name: Download digests
        uses: actions/download-artifact@v4
        with:
          path: /tmp/digests
          pattern: digests-*
          merge-multiple: true
```

`merge-multiple: true` merges `digests-amd64` and `digests-arm64` into the same tree, so `/tmp/digests/app/` ends up containing both arches' digest files (same for `updater`, `nginx`).

Then the manifest step:

```yaml
      - name: Create + push multi-arch manifests
        run: |
          set -euo pipefail
          declare -A IMAGES=(
            [app]="${IMAGE_APP}"
            [updater]="${IMAGE_UPDATER}"
            [nginx]="${IMAGE_NGINX}"
          )
          for name in "${!IMAGES[@]}"; do
            image="${IMAGES[$name]}"
            refs=""
            for f in /tmp/digests/$name/*; do
              digest="$(basename "$f")"   # e.g. sha256:abc...
              refs="$refs ${image}@${digest}"
            done
            echo "Creating manifest for $image from:$refs"
            docker buildx imagetools create \
              -t "${image}:${{ needs.prepare.outputs.full }}" \
              -t "${image}:latest" \
              $refs
            docker buildx imagetools inspect "${image}:${{ needs.prepare.outputs.full }}"
          done
```

Leave the existing "Create git tag" and "Create GitHub release" steps in `merge` as they are (Task 6 adds the changelog step after them).

- [ ] **Step 4: Validate the workflow YAML**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('yaml ok')"
```
Expected: `yaml ok`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: build + push app, updater, and nginx as multi-arch images"
```

---

### Task 6: Auto-update CHANGELOG.md on release + one-time 1.0.4 backfill

**Files:**
- Modify: `.github/workflows/release.yml` (add a step to the `merge` job)
- Modify: `CHANGELOG.md` (one-time backfill)

- [ ] **Step 1: One-time — promote the current `[Unreleased]` to `[1.0.4]`**

In `CHANGELOG.md`, change the line:

```markdown
## [Unreleased]
```

to:

```markdown
## [Unreleased]

## [1.0.4] — 2026-05-31
```

This leaves a fresh empty `## [Unreleased]` at the top and stamps the existing self-update bullets (Added / Changed / Removed) as `1.0.4`.

- [ ] **Step 2: Add the changelog-update step to the `merge` job**

After the "Create GitHub release" step in the `merge` job, append:

```yaml
      - name: Update CHANGELOG.md from release notes
        run: |
          set -euo pipefail
          TAG="${{ needs.prepare.outputs.tag }}"
          VERSION="${{ needs.prepare.outputs.full }}"
          DATE="$(date -u +%Y-%m-%d)"
          # Auto-generated notes from the release we just created.
          NOTES="$(gh release view "$TAG" --json body --jq .body)"
          # Build the new section.
          {
            echo "## [$VERSION] — $DATE"
            echo
            echo "$NOTES"
            echo
          } > /tmp/section.md
          # Insert the new section directly above the first existing versioned
          # heading (## [x.y.z]); keep the file header + [Unreleased] above it.
          awk '
            /^## \[[0-9]/ && !done {
              while ((getline line < "/tmp/section.md") > 0) print line
              done = 1
            }
            { print }
          ' CHANGELOG.md > /tmp/CHANGELOG.md && mv /tmp/CHANGELOG.md CHANGELOG.md
          # Commit back to main. [skip release] + the *.md paths-ignore both
          # prevent this commit from re-triggering the Release workflow.
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add CHANGELOG.md
          git commit -m "docs(changelog): $TAG [skip release]"
          git pull --rebase origin main
          git push origin HEAD:main
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

> The `awk` inserts the new section before the first `## [<digit>` heading — i.e. directly under the `## [Unreleased]` block — so history stays newest-first. `gh release view --json body` returns the same auto-generated notes shown on the GitHub release.

- [ ] **Step 3: Validate the workflow YAML again**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('yaml ok')"
```
Expected: `yaml ok`.

- [ ] **Step 4: Dry-run the awk insertion locally against the real file**

Run (simulates inserting a fake 1.0.5 section without modifying the tracked file):
```bash
printf '## [1.0.5] — 2026-06-01\n\n- example note\n\n' > /tmp/section.md
awk '/^## \[[0-9]/ && !done { while ((getline line < "/tmp/section.md") > 0) print line; done=1 } { print }' CHANGELOG.md | sed -n '1,20p'
```
Expected: the `## [1.0.5]` section appears immediately after the `## [Unreleased]` block and before `## [1.0.4]`. (Visual check only — does not modify `CHANGELOG.md`.)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml CHANGELOG.md
git commit -m "ci: auto-update CHANGELOG on release; stamp 1.0.4"
```

---

### Task 7: README — standalone pull as the primary install + version badge

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a dynamic version badge near the top**

Find the top-of-README badge/title area (read the first ~15 lines first). Add this badge alongside any existing badges (or immediately under the H1 title if there are none):

```markdown
[![Release](https://img.shields.io/github/v/release/digitalXperiments/fluxito?label=release&color=2F5BF4)](https://github.com/digitalXperiments/fluxito/releases/latest)
```

- [ ] **Step 2: Replace the Install section**

Replace the current `## Install` block (the `### Docker (recommended)` git-clone flow and `### Build from source` block) with:

````markdown
## Install

### Run with Docker (recommended — no clone needed)

Download the compose file and start the stack. Every service is pulled as a
prebuilt image from GHCR — no source checkout, no build, static included.

```bash
mkdir fluxito && cd fluxito
curl -O https://raw.githubusercontent.com/digitalXperiments/fluxito/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/digitalXperiments/fluxito/main/.env.example
# Edit .env: set a UPDATER_TOKEN (openssl rand -hex 32) and any secrets you need
docker compose up -d
```

Fluxito is then available on `http://localhost:8000`. Updates are one click from
the in-app **Admin → Updates** panel (super-admin only), or:

```bash
docker compose pull && docker compose up -d
```

### Build from source (developers / forkers)

```bash
git clone https://github.com/digitalXperiments/fluxito.git
cd fluxito
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

This builds the app, updater, and nginx images locally from your checkout instead
of pulling them from GHCR.
````

- [ ] **Step 3: Verify the markdown is well-formed**

Run:
```bash
grep -nE "^\#\#? |docker compose|raw.githubusercontent" README.md | head -20
```
Expected: the new Install section reads top-to-bottom with the pull flow first, build-from-source second; code fences balanced (no stray ``` ). Visually confirm the badge URL and the two `curl` lines are correct.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: standalone docker-pull install + dynamic version badge"
```

---

### Task 8: Final verification

- [ ] **Step 1: Both compose files validate**

Run:
```bash
UPDATER_TOKEN=devtoken docker compose -f docker-compose.yml config >/dev/null && echo "standalone ok"
UPDATER_TOKEN=devtoken docker compose -f docker-compose.yml -f docker-compose.build.yml config >/dev/null && echo "build ok"
```
Expected: `standalone ok` and `build ok`.

- [ ] **Step 2: Standalone compose has no repo-source mounts and no build keys**

Run:
```bash
UPDATER_TOKEN=devtoken docker compose -f docker-compose.yml config | grep -nE "build:|/app/app/static|nginx.conf|/app/docs" || echo "clean: no build keys / no source mounts"
```
Expected: `clean: no build keys / no source mounts`.

- [ ] **Step 3: nginx + updater resolve to published images by default**

Run:
```bash
UPDATER_TOKEN=devtoken docker compose -f docker-compose.yml config | grep -E "image:.*fluxito"
```
Expected: three lines — `ghcr.io/digitalxperiments/fluxito:latest`, `…/fluxito-updater:latest`, `…/fluxito-nginx:latest`.

- [ ] **Step 4: Workflow YAML valid + version logic unchanged**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('yaml ok')"
git fetch --tags -q
TRACK=$(tr -d '[:space:]' < VERSION)
LATEST=$(git tag --list "v${TRACK}.*" | sed "s/^v${TRACK}\.//" | grep -E '^[0-9]+$' | sort -n | tail -1)
echo "next release on merge = ${TRACK}.$((LATEST+1))"
```
Expected: `yaml ok`; next release = `1.0.5` (highest tag is now `v1.0.4`).

---

## Self-Review Notes (coverage check)

- Standalone no-clone pull (all images from GHCR, static included) → Tasks 1, 2, 3, 5. ✓
- nginx config baked + `/static` proxied to app (no host mount) → Tasks 1, 2. ✓
- `./docs` mount removed (tutorials baked in image) → Task 3 Step 2. ✓
- updater published as an image → Tasks 3, 5. ✓
- Build-from-source secondary path → Task 4 + README Task 7. ✓
- Three multi-arch images + manifests in CI → Task 5. ✓
- CHANGELOG auto-generated from commits on release + 1.0.4 backfill → Task 6. ✓
- README primary = docker pull, secondary = build; dynamic version badge → Task 7. ✓
- **Known limitation (documented, not a task):** self-update recreates only the `app` container, so a release that changes `nginx.conf` or the updater image won't take effect until the operator runs `docker compose pull && up -d`. nginx config changes are rare; acceptable for v1.
