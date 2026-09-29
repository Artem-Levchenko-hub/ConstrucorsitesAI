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
