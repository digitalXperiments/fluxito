# syntax=docker/dockerfile:1.7
#
# Multi-stage build optimized for layer caching and minimal image size.
# Builder stage compiles wheels with build tools, runtime stage includes only
# runtime dependencies. Final image: ~450MB (asyncpg, cryptography, WeasyPrint native deps).
#
# ── Build stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install build dependencies only (removed from runtime stage).
# libpq-dev, libffi-dev, build-essential are compiler toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies to /install prefix (easier to copy).
# COPY requirements.txt first so code changes don't invalidate this layer.
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt


# ── Runtime stage ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Create non-root app user early.
RUN groupadd --system --gid 1000 app && \
    useradd --system --uid 1000 --gid app --create-home --shell /bin/bash app

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production

# Runtime dependencies only (no compiler toolchain).
# curl is needed for healthchecks; libpango/libharfbuzz for WeasyPrint runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    ca-certificates \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libharfbuzz-subset0 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy prebuilt Python packages from builder stage (no recompilation needed).
COPY --from=builder /install /usr/local

# Copy application code (tests, .env, docs, etc. excluded via .dockerignore).
COPY --chown=app:app . .

# Ensure start.sh is executable and owned by app user.
RUN chmod +x start.sh && chown -R app:app /app

# Run as non-root user (never root in production).
USER app

EXPOSE 8001

# Health check: curl /api/health. Tolerates 45s startup for migrations.
# Consecutive failures trigger restart per docker-compose restart policy.
HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=45s \
    CMD curl -fsS http://localhost:8001/api/health || exit 1

CMD ["./start.sh"]
