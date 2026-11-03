"""Production OAuth uses the canonical return URL and API-only platform credentials."""

from pathlib import Path

import yaml


def test_yookassa_oauth_configuration_is_canonical_and_api_only():
    root = Path(__file__).resolve().parents[3]
    services = yaml.safe_load((root / "apps/llm-gateway/deploy/full/docker-compose.yml").read_text(
        encoding="utf-8",
    ))["services"]
    api = services["api"]["environment"]
    assert api["INTEGRATION_OAUTH_CALLBACK_BASE_URL"] == (
        "${INTEGRATION_OAUTH_CALLBACK_BASE_URL:-https://yleum.ru/api/integrations/oauth}"
    )
    assert api["WEB_BASE_URL"] == "${WEB_BASE_URL:-https://yleum.ru}"
    for field in ("INTEGRATION_YOOKASSA_CLIENT_ID", "INTEGRATION_YOOKASSA_CLIENT_SECRET"):
        assert api[field] == "${" + field + ":-}"
        for name, service in services.items():
            if name != "api":
                assert service.get("environment", {}).get(field) in (None, ""), (name, field)
