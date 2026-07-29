from omnia_api.services.agent_progress import (
    REDACTED,
    redact_sensitive_text,
    sanitize_agent_steps,
)


def test_redacts_shell_assignments_dsn_and_tokens() -> None:
    source = "\n".join(
        [
            "export AUTH_SECRET='auth-value'",
            "DATABASE_URL=postgresql://app:db-password@postgres:5432/app",
            "MINIO_ACCESS_KEY=access-value",
            "Authorization: Bearer bearer-value",
            "token=sk-exampleexampleexampleexample",
        ]
    )

    result = redact_sensitive_text(source)

    assert "auth-value" not in result
    assert "db-password" not in result
    assert "access-value" not in result
    assert "bearer-value" not in result
    assert "sk-example" not in result
    assert result.count(REDACTED) >= 5


def test_public_transcript_sanitizer_preserves_safe_shape() -> None:
    rows = sanitize_agent_steps(
        [
            {
                "step": 1,
                "kind": "step",
                "action": "Выполняю команду",
                "tool": "bash",
                "path": "",
                "detail": "AUTH_SECRET=do-not-show\nbuild clean",
                "ok": True,
            }
        ]
    )

    assert rows is not None
    assert rows[0]["step"] == 1
    assert rows[0]["ok"] is True
    assert rows[0]["detail"] == f"AUTH_SECRET={REDACTED}\nbuild clean"
