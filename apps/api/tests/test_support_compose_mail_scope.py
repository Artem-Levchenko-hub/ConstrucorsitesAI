"""New inbox settings reach consumers without leaking through Compose anchors."""

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def services():
    root = Path(__file__).resolve().parents[3]
    path = root / "apps/llm-gateway/deploy/full/docker-compose.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize("role", ["api", "worker"])
def test_support_mail_consumers_receive_the_same_explicit_configuration(services, role):
    expected = {
        "SUPPORT_SMTP_FROM": "${SUPPORT_SMTP_FROM:-}",
        "SUPPORT_IMAP_HOST": "${SUPPORT_IMAP_HOST:-}",
        "SUPPORT_IMAP_PORT": "${SUPPORT_IMAP_PORT:-993}",
        "SUPPORT_IMAP_USER": "${SUPPORT_IMAP_USER:-}",
        "SUPPORT_IMAP_PASSWORD": "${SUPPORT_IMAP_PASSWORD:-}",
        "SUPPORT_IMAP_MAILBOX": "${SUPPORT_IMAP_MAILBOX:-INBOX}",
        "SUPPORT_IMAP_RECIPIENT": "${SUPPORT_IMAP_RECIPIENT:-support@yleum.ru}",
        "SUPPORT_IMAP_START_UID": "${SUPPORT_IMAP_START_UID:-}",
        "LEGAL_SUPPORT_EMAIL": "${LEGAL_SUPPORT_EMAIL:-support@yleum.ru}",
    }
    environment = services[role]["environment"]
    assert {name: environment.get(name) for name in expected} == expected
    # The worker opens capabilities encrypted by the API and signs reply threads.
    for name in ("JWT_SECRET", "SECRETS_ENCRYPTION_KEY"):
        assert environment[name] == services["api"]["environment"][name]


def test_inbox_configuration_is_not_inherited_by_generation_or_public_services(services):
    private_fields = (
        "SUPPORT_IMAP_HOST",
        "SUPPORT_IMAP_USER",
        "SUPPORT_IMAP_PASSWORD",
        "SUPPORT_IMAP_MAILBOX",
        "SUPPORT_IMAP_RECIPIENT",
        "SUPPORT_IMAP_START_UID",
    )
    for role, service in services.items():
        if role in {"api", "worker"}:
            continue
        environment = service.get("environment", {})
        for name in private_fields:
            assert environment.get(name) in (None, ""), (role, name)
