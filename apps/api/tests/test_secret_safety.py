from __future__ import annotations

from omnia_api.services.secret_safety import (
    contains_provider_secret,
    is_secret_file,
    max_model_write_rejection,
    redact_provider_secrets,
)


def test_secret_detector_is_high_confidence() -> None:
    assert contains_provider_secret("sk-" + "a" * 24)
    assert contains_provider_secret("AIza" + "a" * 32)
    assert not contains_provider_secret("Use process.env.DEEPSEEK_API_KEY")
    assert not contains_provider_secret("подключи API-ключ через интеграции")
    raw = "подключи " + "sk-" + "a" * 24
    assert redact_provider_secrets(raw) == "подключи [CREDENTIAL REDACTED]"


def test_secret_detector_blocks_and_redacts_labelled_provider_tokens() -> None:
    raw = "AITUNNEL API key: provider_token_1234567890"

    assert contains_provider_secret(raw)
    safe = redact_provider_secrets(raw)
    assert "provider_token_1234567890" not in safe
    assert "[CREDENTIAL REDACTED]" in safe


def test_max_writer_blocks_secret_files_and_literals() -> None:
    assert is_secret_file(".env.local")
    assert is_secret_file("config/secrets.json")
    assert not is_secret_file("src/components/EnvironmentBadge.tsx")
    assert ".env" in str(max_model_write_rejection(".env.local", "VALUE=hidden"))
    assert "blocked" in str(
        max_model_write_rejection("src/lib/provider.ts", 'const key = "sk-' + "b" * 24 + '"')
    )
    assert (
        max_model_write_rejection("src/app/page.tsx", "export default function Page() {}") is None
    )
