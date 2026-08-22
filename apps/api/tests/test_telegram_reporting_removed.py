from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

REMOVED_FILES = (
    "apps/api/scripts/dev_generation_telegram_acceptance.py",
    "apps/api/src/omnia_api/models/generation_telegram_report.py",
    "apps/api/src/omnia_api/services/generation_telegram_delivery.py",
    "apps/api/src/omnia_api/services/generation_telegram_reports.py",
    "apps/api/src/omnia_api/workers/generation_reports.py",
    "infra/monitoring/telegram_generation_report.py",
)

REPORTING_SURFACES = (
    ".github/workflows/production-generation-canary.yml",
    "apps/llm-gateway/deploy/full/docker-compose.yml",
    "infra/.env.example",
    "infra/ci/README.md",
)


def test_reporting_modules_are_absent() -> None:
    remaining = [name for name in REMOVED_FILES if (ROOT / name).exists()]

    assert remaining == []


def test_production_surfaces_do_not_reference_telegram_reporting() -> None:
    forbidden = (
        "generation-report-worker",
        "DEV_GENERATION_TELEGRAM_REPORTS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "telegram_generation_report.py",
    )

    for name in REPORTING_SURFACES:
        text = (ROOT / name).read_text(encoding="utf-8")
        remaining = [token for token in forbidden if token in text]
        assert remaining == [], name
