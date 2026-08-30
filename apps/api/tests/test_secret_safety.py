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


def test_labelled_arbitrary_credential_is_detected_and_redacted() -> None:
    credential = "abcdefghijklmnop.qrstuvwxyz123456"
    raw = f"AITUNNEL — ключ: {credential}"

    assert contains_provider_secret(raw)
    assert redact_provider_secrets(raw) == "AITUNNEL — ключ: [CREDENTIAL REDACTED]"


def test_telegram_bot_token_is_detected_without_a_label() -> None:
    credential = "12345678:" + "A" * 30
    raw = f"bot credential {credential}"

    assert contains_provider_secret(raw)
    assert redact_provider_secrets(raw) == "bot credential [CREDENTIAL REDACTED]"


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


def test_max_writer_blocks_package_manifest_script_drift_and_lifecycle_hooks() -> None:
    altered_dev = """
    {
      "packageManager": "pnpm@9.15.0",
      "scripts": {
        "dev": "vite",
        "build": "next build",
        "start": "next start --port 3000 --hostname 0.0.0.0"
      }
    }
    """
    lifecycle_hook = """
    {
      "packageManager": "pnpm@9.15.0",
      "scripts": {
        "dev": "next dev --turbopack --port 3000 --hostname 0.0.0.0",
        "build": "next build",
        "start": "next start --port 3000 --hostname 0.0.0.0",
        "postinstall": "node steal.js"
      }
    }
    """

    assert "platform-managed" in str(max_model_write_rejection("package.json", altered_dev))
    assert "lifecycle hooks" in str(max_model_write_rejection("package.json", lifecycle_hook))


def test_max_writer_blocks_non_registry_dependency_sources() -> None:
    git_dep = """
    {
      "packageManager": "pnpm@9.15.0",
      "scripts": {
        "dev": "next dev --turbopack --port 3000 --hostname 0.0.0.0",
        "build": "next build",
        "start": "next start --port 3000 --hostname 0.0.0.0"
      },
      "dependencies": {
        "left-pad": "git+ssh://example.com/private.git"
      }
    }
    """
    safe_manifest = """
    {
      "packageManager": "pnpm@9.15.0",
      "scripts": {
        "dev": "next dev --turbopack --port 3000 --hostname 0.0.0.0",
        "build": "next build",
        "start": "next start --port 3000 --hostname 0.0.0.0"
      },
      "dependencies": {
        "zod": "^4.0.0"
      }
    }
    """

    assert "npm-registry version" in str(max_model_write_rejection("package.json", git_dep))
    assert max_model_write_rejection("package.json", safe_manifest) is None
