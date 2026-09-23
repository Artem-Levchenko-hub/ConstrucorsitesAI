# syntax=docker/dockerfile:1
# Platform stack templates (SYSTEM_PROMPT.md, .omnia/skills, skeleton sources)
# are BAKED into the image at /orchestrator/templates, so the api/worker images
# run on any host without a bind mount of the repo checkout (portable images,
# Phase 0 of the 13-VPS move). The templates live outside this build context,
# so they arrive as a named build context:
#     docker build --build-context templates=../orchestrator/templates .
# Compose passes it via `additional_contexts`. Without it this fallback stage
# runs and fails loudly — a silent template-less image is exactly the old
# "every agentic build falls back to the entities prompt" bug.
FROM busybox:1.36 AS templates
RUN echo "missing build context: pass --build-context templates=<repo>/apps/orchestrator/templates" >&2 && exit 1

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy
# UV_COMPILE_BYTECODE intentionally disabled — on small VPS the parallel .pyc
# compilation step regularly timed out (>60s on a single .py file). Cold start
# is a bit slower without precompiled .pyc, but each module is bytecoded on
# first import and cached, so warm requests pay nothing.

# Системные зависимости: libgit2 для pygit2 + браузерные либы для Playwright Chromium.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgit2-dev \
    git \
    curl \
    ca-certificates \
    ffmpeg \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Browser dependencies depend on the lockfile, not application source. Keep
# this expensive layer cached when shipping a Python-only runtime fix.
RUN .venv/bin/playwright install chromium --with-deps || .venv/bin/playwright install chromium

COPY . ./
RUN uv sync --frozen

# Sources only: dependency trees and build output are regenerated per project
# by the orchestrator and would add ~1 GB to the image.
COPY --from=templates \
     --exclude=**/node_modules --exclude=**/.next --exclude=**/.git \
     --exclude=**/*.tsbuildinfo --exclude=**/__pycache__ \
     . /orchestrator/templates/

# Образ работает не только под root: биллинговый воркер в K3s commerce запускается от uid 10001
# (Pod Security «restricted»). COPY переносит права файлов из контекста сборки как есть, и файл,
# созданный на хосте под umask 077, остаётся 0600 → «Permission denied» при импорте (23.09.2026,
# core/redis.py). Нормализуем чтение для всех только там, где его нет; .venv не трогаем, чтобы
# не копировать слой браузера.
RUN find /app /orchestrator/templates -path /app/.venv -prune -o \
      \( -type d ! -perm -o=rx -exec chmod a+rx {} + \) -o \
      \( -type f ! -perm -o=r -exec chmod a+r {} + \)

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "omnia_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
