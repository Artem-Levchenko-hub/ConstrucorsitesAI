from __future__ import annotations

from pathlib import Path

COMPOSE = (
    Path(__file__).parents[2] / "llm-gateway/deploy/full/docker-compose.yml"
).read_text(encoding="utf-8")


def _service(name: str, next_name: str) -> str:
    start = COMPOSE.index(f"  {name}:\n")
    marker = "\nvolumes:" if next_name == "volumes" else f"  {next_name}:\n"
    end = COMPOSE.index(marker, start)
    return COMPOSE[start:end]


def test_generation_report_worker_is_isolated_and_reuses_api_image() -> None:
    api = _service("api", "worker")
    worker = _service("worker", "generation-report-worker")
    reports = _service("generation-report-worker", "web")
    web = _service("web", "volumes")
    gateway = _service("gateway", "api")

    assert "image: ${API_IMAGE:-omnia-api:prod}" in reports
    assert (
        'command: ["/app/.venv/bin/python", "-m", '
        '"omnia_api.workers.generation_reports"]'
    ) in reports
    assert "container_name: omnia-prod-generation-report-worker" in reports
    assert "api:\n        condition: service_healthy" in reports
    assert "postgres:\n        condition: service_healthy" in reports
    assert "minio-init:\n        condition: service_completed_successfully" in reports
    assert "redis:" not in reports
    assert "REDIS_URL:" not in reports
    assert "restart: unless-stopped" in reports

    assert "DEV_GENERATION_TELEGRAM_REPORTS: ${DEV_GENERATION_TELEGRAM_REPORTS:-false}" in api
    assert (
        "DEV_GENERATION_TELEGRAM_REPORTS: ${DEV_GENERATION_TELEGRAM_REPORTS:-false}"
        in worker
    )
    assert (
        "DEV_GENERATION_TELEGRAM_REPORTS: ${DEV_GENERATION_TELEGRAM_REPORTS:-false}"
        in reports
    )
    assert "TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}" in reports
    assert "TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID:-0}" in reports
    for untrusted in (api, worker, web, gateway):
        assert "TELEGRAM_BOT_TOKEN:" not in untrusted
        assert "TELEGRAM_CHAT_ID:" not in untrusted


def test_generation_report_worker_has_only_required_database_and_minio_runtime() -> None:
    reports = _service("generation-report-worker", "web")

    assert "OMNIA_RELEASE_SHA: ${OMNIA_RELEASE_SHA:-unknown}" in reports
    assert "DATABASE_URL: postgresql+asyncpg://" in reports
    assert "MINIO_ENDPOINT: minio:9000" in reports
    assert "MINIO_ACCESS_KEY: ${MINIO_ROOT_USER:-omnia}" in reports
    assert (
        "MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD:-omnia-minio-secret-please-change}"
        in reports
    )
    assert "MINIO_BUCKET_PREVIEWS: ${MINIO_BUCKET_PREVIEWS:-previews}" in reports
    assert "JWT_SECRET: ${JWT_SECRET:?set JWT_SECRET in .env}" in reports
    assert "LLMGW_API_KEY:" not in reports
    assert "ORCHESTRATOR_INTERNAL_TOKEN:" not in reports
