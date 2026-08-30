# Project Instructions & Memory: Fluxito

## Git & Release Policies (CRITICAL)

1. **NEVER PUSH TO GITHUB UNLESS EXPLICITLY INSTRUCTED BY THE USER.**
   - Do NOT run `git push` autonomously.
   - Wait for the user to explicitly ask (e.g. "push the changes to github" or "push to main").

2. **Commit Only on Explicit Request**:
   - Do not make frequent unrequested commits.
   - Batch changes and commit only when the user requests or when preparing a user-approved release.

3. **Pre-Push Quality Gate**:
   - Before any push, always execute `tox -e lint` locally (verifying `ruff check` and `ruff format --check`).
   - Fix all lint and formatting issues before asking to push.

## Tech Stack & Architecture

- **Backend**: FastAPI, SQLAlchemy (Async), Alembic, PostgreSQL, Redis.
- **AI Subsystem**: Anthropic, OpenAI, Google Gemini, xAI Grok, Mistral, LM Studio (OpenAI-compatible).
- **Frontend**: Jinja2 templates, modern responsive CSS, vanilla JavaScript components.
