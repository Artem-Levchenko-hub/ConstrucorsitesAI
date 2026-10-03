from types import SimpleNamespace

import pytest

from yleum_api.services.generation_failure import public_generation_failure


def test_execution_revision_is_recorded_without_overwriting_other_state(monkeypatch):
    from yleum_api.core.config import get_settings
    from yleum_api.services.generation_runs import note_generation_release

    monkeypatch.setenv("YLEUM_RELEASE_SHA", "a" * 40)
    get_settings.cache_clear()
    run = SimpleNamespace(agent_state={"preserved": 1})
    note_generation_release(run)
    assert run.agent_state == {"preserved": 1, "runtime_revision": "a" * 40}
    monkeypatch.delenv("YLEUM_RELEASE_SHA")
    get_settings.cache_clear()


def test_long_build_log_keeps_terminal_error_and_redacts_before_truncation():
    from yleum_api.services.agent_progress import bounded_redacted_diagnostic

    raw = "MAX_FINALIZATION_FAILED: compiled successfully\n" + "route table\n" * 1000
    raw += "DATABASE_URL=postgresql://user:TOP_SECRET@db:5432/app\nBUILD_RECEIPT_MISSING"
    detail = bounded_redacted_diagnostic(raw, max_bytes=2000)
    assert detail.startswith("MAX_FINALIZATION_FAILED")
    assert detail.endswith("BUILD_RECEIPT_MISSING")
    assert "TOP_SECRET" not in detail
    assert len(detail.encode()) <= 2000


@pytest.mark.parametrize("status", ["completed", "cancelled", "running"])
def test_nonfailure_has_no_error_card(status):
    assert public_generation_failure(SimpleNamespace(status=status, error="deadline")) is None


def test_provider_timeout_is_not_reported_as_a_failed_build_check():
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error="PROVIDER_TIMEOUT: synthetic-private-detail", agent_state={},
    ))
    assert failure.code == "provider_unavailable"
    assert "провайдер модели не ответил вовремя" in failure.message.casefold()
    assert "Автоматический повтор остановлен" in failure.message
    assert "synthetic-private" not in failure.model_dump_json()


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        ("generation deadline exceeded; phase=fast_check; secret=do-not-expose", "deadline", True),
        ("PROVIDER_AUTH_FAILED: key=do-not-expose", "provider_access", False),
        ("PROVIDER_UNAVAILABLE: url=do-not-expose", "provider_unavailable", True),
        ("edit produced no source changes", "no_changes", True),
        ("MAX migration contract unsafe DROP", "data_contract", True),
        ("unknown password=do-not-expose", "verification", True),
    ],
)
def test_history_failure_is_allowlisted_and_never_echoes_raw_error(error, code, retryable):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error=error, agent_state={})
    )
    assert failure.code == code
    assert failure.retryable is retryable
    assert "do-not-expose" not in failure.model_dump_json()


def test_restoration_retry_must_use_reviewed_version_workflow():
    failure = public_generation_failure(
        SimpleNamespace(
            status="failed",
            error="deadline exceeded",
            agent_state={"restoration_adaptation": {"operation_id": "opaque"}},
        )
    )
    assert not failure.retryable
    assert "историю версий" in failure.message


@pytest.mark.parametrize(
    "wrapper", ["[Ошибка: password=NEVER_PUBLISH]", "[Ошибка генерации: token=NEVER_PUBLISH]"]
)
def test_legacy_service_error_is_projected_without_rewriting_user_or_partial_text(wrapper):
    from yleum_api.services.generation_failure import public_message_content

    assert "NEVER_PUBLISH" not in public_message_content("assistant", wrapper)
    assert public_message_content("assistant", wrapper, has_failure=True) == ""
    assert public_message_content("user", wrapper) == wrapper
    assert public_message_content("assistant", "real partial response") == "real partial response"


def test_empty_failed_build_never_persists_the_exception_secret():
    from yleum_api.services.generation.agent_messages import _failed_build_body

    assert "NEVER_PUBLISH" not in _failed_build_body(
        "", "PROVIDER_AUTH_FAILED: token=NEVER_PUBLISH"
    )


@pytest.mark.parametrize("has_failure", [True, False])
@pytest.mark.parametrize("prefix", ["Ошибка", "Ошибка генерации"])
def test_legacy_wrapper_keeps_real_response_suffix(prefix, has_failure):
    from yleum_api.services.generation_failure import public_message_content

    suffix = "Готовая часть ответа…\nСледующий абзац."
    content = f"[{prefix}: password=NEVER_PUBLISH]\n{suffix}"
    projected = public_message_content("assistant", content, has_failure=has_failure)
    assert projected.endswith(suffix)
    assert "NEVER_PUBLISH" not in projected
    if has_failure:
        assert projected == suffix


@pytest.mark.parametrize("has_failure", [True, False])
def test_truncated_legacy_wrapper_never_exposes_diagnostic_tail(has_failure):
    from yleum_api.services.generation_failure import public_message_content

    content = "[Ошибка: token=NEVER_PUBLISH\ntruncated secret=NEVER_PUBLISH"
    assert "NEVER_PUBLISH" not in public_message_content(
        "assistant", content, has_failure=has_failure
    )
