# syntax=docker/dockerfile:1.7
# AION backend image. Runs as an unprivileged user; secrets only via env/secret files.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build-time deps for wheels that may need compiling, removed in the final layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY alembic.ini ./
COPY aion ./aion
COPY alembic ./alembic
COPY bootstrap_admin.py ./bootstrap_admin.py

RUN pip install --no-cache-dir . \
    && apt-get purge -y --auto-remove build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user.
RUN groupadd --gid 10001 aion \
    && useradd --uid 10001 --gid aion --create-home --shell /usr/sbin/nologin aion \
    && chown -R aion:aion /app
USER aion

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# Migrations are applied by the entrypoint wrapper so a fresh volume works.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn aion.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
