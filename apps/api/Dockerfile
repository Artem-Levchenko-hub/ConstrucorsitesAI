# syntax=docker/dockerfile:1.6
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Системные зависимости: libgit2 для pygit2 + браузерные либы для Playwright Chromium.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgit2-dev \
    git \
    curl \
    ca-certificates \
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

COPY pyproject.toml ./
RUN uv sync --no-install-project

COPY . ./
RUN uv sync && uv run playwright install chromium --with-deps || uv run playwright install chromium

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "omnia_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
