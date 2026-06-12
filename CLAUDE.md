# Fluxito — Project Instructions

## Committing while working on a branch

Do **not** commit after every small change, and don't make frequent
checkpoint-style commits on your own. Make the edits and pause — **commit only when
I explicitly ask.** When I say "commit", batch the outstanding work into a sensible
commit (or a few well-scoped commits) with a good message. Pushing then follows the
Releasing policy below.

## Before any `git push`: `tox` must be green (HARD GATE)

**Never push to any branch with a red `tox`.** Before every `git push`, run the full
local CI and confirm all three environments pass:

```bash
tox            # runs: lint, typecheck, test  (in that order)
```

- **lint** — `ruff check app tests` **and** `ruff format --check app tests`. CI uses the
  pinned `ruff==0.8.4`; after editing any file, run `ruff format <files>` so the
  format-check passes (a common miss — `ruff check` can pass while `ruff format --check`
  fails on lines you added).
- **typecheck** — `mypy` on the pinned critical modules.
- **test** — `pytest` (needs Postgres + Redis running locally).

If any environment fails, **fix it and re-run `tox` until green before pushing.** Do not
push and "let CI catch it." If only part changed, you may scope it (`tox -e lint`,
`tox -e lint,typecheck`), but a full `tox` must be green before the push that ships.

## Releasing: pushing to `main`

Every push to `main` cuts a release: the GitHub Actions **Release** workflow builds
the multi-arch images (`fluxito`, `fluxito-updater`, `fluxito-nginx`), tags
`vX.Y.Z`, and publishes a GitHub Release **whose notes are read verbatim from the
matching `CHANGELOG.md` section**. No bot ever commits docs back to `main` — that
is done here, by you, before the push.

**When the user asks to push to `main`, do ALL of this BEFORE running `git push`:**

1. **Compute the next version** (identical to the workflow's logic):
   ```bash
   git fetch --tags -q
   TRACK=$(tr -d '[:space:]' < VERSION)                       # e.g. 1.0
   LATEST=$(git tag --list "v${TRACK}.*" | sed "s/^v${TRACK}\.//" \
            | grep -E '^[0-9]+$' | sort -n | tail -1)
   echo "${TRACK}.$(( ${LATEST:--1} + 1 ))"                   # e.g. 1.0.6
   ```
   (To bump the minor/major, the user edits the `VERSION` track file, e.g. `1.0` → `1.1`.)

2. **Update `CHANGELOG.md`**: rename the top `## [Unreleased]` to
   `## [<NEXT>] — <YYYY-MM-DD>` and write **curated, human-readable** entries
   (`### Added` / `### Changed` / `### Fixed` / `### Removed`) describing what this
   push contains — not raw commit subjects. Leave a fresh empty `## [Unreleased]`
   above it. These exact lines become the GitHub Release notes, so write them for
   humans.

3. **Update `README.md`** only if the change affects what it documents (install,
   features, configuration, ports). Most pushes won't. The version badge is dynamic
   — never hand-edit a version number anywhere.

4. **Commit** the doc updates (and the code), then push to `main`.

**Escape hatch:** to push to `main` without cutting a release / changelog entry,
put `[skip release]` in the commit message.

A `PreToolUse` guard (`.claude/changelog-push-guard.sh`) blocks a push to `main`
when the pending commits don't touch `CHANGELOG.md` (unless the latest commit
message contains `[skip release]`), as a reminder to follow this policy.
