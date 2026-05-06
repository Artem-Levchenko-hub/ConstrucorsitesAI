# syntax=docker/dockerfile:1.6
#
# Multi-stage image for the Omnia LLM Gateway.
#
# Build: `docker build -t omnia-gateway .` (run from apps/llm-gateway/)
# Run:   `docker run --env-file .env -p 8001:8001 omnia-gateway`
#
# Stage 1 installs deps via uv into a project-local .venv.
# Stage 2 ships only the venv + source — no uv, no build tools.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# uv is the only thing we need to bootstrap deps.
RUN pip install --no-cache-dir "uv>=0.5"

# Layer 1: lock + manifest only — caches deps when only source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Layer 2: source — invalidated on every code change.
COPY src ./src
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# curl is used by the HEALTHCHECK below; ca-certificates lets httpx talk TLS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src /app/src

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD curl -fs http://localhost:8001/health || exit 1

CMD ["uvicorn", "omnia_gateway.main:app", \
     "--host", "0.0.0.0", "--port", "8001", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--loop", "asyncio"]
