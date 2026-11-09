"""A finalization failure retains both causes while rollback stays unverified."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from yleum_api.services import restorations
from yleum_api.services.generation import agent_finalization, agent_recovery
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.generation.recovery_trace import bounded_recovery_diagnostic


@pytest.fixture
def finalization_failure(monkeypatch):
    original = RuntimeError("generation deadline exceeded during source repair")
    rollback = RuntimeError("migration receipt missing; SQLSTATE 42P01")
    accepted, candidate = {"src/app/page.tsx": "accepted"}, {"src/app/page.tsx": "candidate"}
    events = []
    monkeypatch.setattr(restorations, "adaptation_activation_holds_generation_lease",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(agent_finalization.repo_svc, "read_files", lambda *args: accepted)
    restore = AsyncMock(side_effect=rollback)
    monkeypatch.setattr(agent_finalization, "_restore_project_cell_source", restore)
    runtime = GenerationRuntime(
        handle=SimpleNamespace(snapshot_files=AsyncMock(return_value=candidate),
                               prove_restoration_adaptation=None),
        coordinator=SimpleNamespace(finalize_with_repair=AsyncMock(side_effect=original),
                                    session_factory=None),
    )
    async def emit(event, payload):
        events.append((event, payload))
    kwargs = dict(
        _is_edit=True, _max_has_generated_snapshot=True, _max_shell_enabled=False,
        accumulated="candidate", baseline=SourceBaseline(uuid4(), "a" * 40, accepted),
        files=candidate, ids=GenerationIds(*(uuid4() for _ in range(5))), is_free=False,
        prompt_text="change design", runtime=runtime, plan=SimpleNamespace(),
        operations=SimpleNamespace(emit=emit), source_handoff=True,
    )
    return kwargs, original, rollback, restore, events


async def test_finalization_error_keeps_primary_and_secondary_cause(finalization_failure):
    kwargs, original, rollback, restore, events = finalization_failure
    with pytest.raises(RuntimeError) as raised:
        await agent_finalization.finalize_max_candidate(**kwargs)
    detail = str(raised.value)
    assert str(original) in detail and str(rollback) in detail
    assert "rollback_unverified" in detail
    assert restore.await_count == 1
    assert raised.value.diagnostics["original_error"] == str(original)
    assert raised.value.diagnostics["rollback_error"] == str(rollback)
    event, payload = events[0]
    assert event == "agent.step" and payload["ok"] is False
    assert payload["recovery_trace"]["run_id"] == str(kwargs["ids"].run_id)
    assert payload["detail"] == detail


async def test_both_causes_and_chain_are_redacted_and_bounded(finalization_failure):
    kwargs, _, _, restore, _ = finalization_failure
    original = (
        "primary failure\nLLMGW_API_KEY=synthetic-primary-secret\n" + "x" * 9000 + "\nprimary-tail"
    )
    secondary = (
        "rollback failure\nDATABASE_URL=postgres://u:synthetic-db-secret@host/db\n"
        + "y" * 9000 + "\nSQLSTATE 42P01"
    )
    kwargs["runtime"].coordinator.finalize_with_repair.side_effect = RuntimeError(original)
    restore.side_effect = RuntimeError(secondary)
    with pytest.raises(RuntimeError) as raised:
        await agent_finalization.finalize_max_candidate(**kwargs)
    detail = str(raised.value)
    assert "primary failure" in detail and "primary-tail" in detail
    assert "rollback failure" in detail and "SQLSTATE 42P01" in detail
    assert "synthetic-primary-secret" not in detail and "synthetic-db-secret" not in detail
    assert "synthetic-db-secret" not in str(raised.value.__cause__)
    assert len(detail.encode()) < 5000


@pytest.mark.parametrize("unverified", [False, 1, "ready"])
async def test_first_product_red_receipt_keeps_actual_operation_and_false_status(
    finalization_failure, monkeypatch, unverified,
):
    kwargs, _, _, _, events = finalization_failure
    operation = uuid4()
    kwargs.update(_is_edit=False, _max_has_generated_snapshot=False,
                  baseline=SourceBaseline(uuid4(), "", {}),
                  render_core_on_rejection=AsyncMock(return_value={}))
    kwargs["runtime"].handle.export_files = AsyncMock(return_value=kwargs["files"])
    kwargs["operations"].probe_build = AsyncMock()
    monkeypatch.setattr(agent_finalization, "_restore_max_core", AsyncMock(return_value={
        "ok": unverified, "detail": "actual migration controller failure",
        "operation_id": str(operation),
    }))
    with pytest.raises(RuntimeError, match="rollback_unverified") as raised:
        await agent_finalization.finalize_max_candidate(**kwargs)
    assert "actual migration controller failure" in str(raised.value)
    assert str(operation) in str(raised.value)
    assert raised.value.diagnostics["operation_id"] == str(operation)
    assert events[0][1]["recovery_trace"]["operation_id"] == str(operation)
    assert events[0][1]["ok"] is False


async def test_failed_diagnostic_publication_does_not_replace_actual_failure(finalization_failure):
    kwargs, original, rollback, _, _ = finalization_failure
    kwargs["operations"].emit = AsyncMock(side_effect=RuntimeError("private event failure"))
    with pytest.raises(RuntimeError) as raised:
        await agent_finalization.finalize_max_candidate(**kwargs)
    assert str(original) in str(raised.value) and str(rollback) in str(raised.value)
    assert "private event failure" not in str(raised.value)


async def test_runtime_verification_returns_the_actual_operation_identity():
    restore = AsyncMock(return_value=SimpleNamespace(ok=False, redacted_detail="red controller"))
    result = await agent_recovery._verify_restored_runtime(
        SimpleNamespace(run_role=restore), AsyncMock(), source_only=True,
    )
    assert UUID(result["operation_id"]) == restore.await_args.args[1]
    assert result["ok"] is False and result["detail"] == "red controller"


async def test_runtime_exception_retains_the_actual_dispatched_operation_identity():
    failure = RuntimeError("controller reply unavailable")
    restore = AsyncMock(side_effect=failure)
    with pytest.raises(RuntimeError) as raised:
        await agent_recovery._verify_restored_runtime(
            SimpleNamespace(run_role=restore), AsyncMock(), source_only=True,
        )
    assert raised.value is failure
    assert raised.value._rollback_operation_id == restore.await_args.args[1]


async def test_finalization_preserves_operation_from_unknown_rollback_exception(
    finalization_failure,
):
    kwargs, _, rollback, _, _ = finalization_failure
    operation = uuid4()
    rollback._rollback_operation_id = operation
    with pytest.raises(RuntimeError) as raised:
        await agent_finalization.finalize_max_candidate(**kwargs)
    assert raised.value.diagnostics["operation_id"] == str(operation)


@pytest.mark.parametrize("side", ["original", "rollback"])
@pytest.mark.parametrize("assignment", [
    "API_KEY=private-inline-credential",
    "api-key='private-inline-credential'",
    "SECRET_KEY=private-inline-credential",
    "MAX_TOKEN_PREVIOUS=private-inline-credential",
    '"SECRET_KEY": "private-inline-credential"',
    "Authorization: Bearer private-inline-credential",
    "cookie=session=private-inline-credential",
])
async def test_inline_credentials_are_redacted_before_every_failure_surface(
    finalization_failure, caplog, side, assignment,
):
    kwargs, _, _, restore, events = finalization_failure
    cause = RuntimeError("cause prefix " + assignment + "\n" + "ы" * 5000 + " cause-tail")
    if side == "original":
        kwargs["runtime"].coordinator.finalize_with_repair.side_effect = cause
    else:
        restore.side_effect = cause
    with pytest.raises(RuntimeError) as raised:
        await agent_finalization.finalize_max_candidate(**kwargs)
    surfaces = [str(raised.value), str(raised.value.__cause__),
                str(raised.value.diagnostics), str(events)]
    surfaces.extend(str(getattr(record, field, "")) for record in caplog.records
                    for field in ("original_error", "rollback_error"))
    assert all("private-inline-credential" not in value for value in surfaces)
    assert "cause prefix" in str(raised.value) and "cause-tail" in str(raised.value)
    assert "[REDACTED]" in str(raised.value) and len(str(raised.value).encode()) < 5000


@pytest.mark.parametrize("side", ["original", "rollback"])
@pytest.mark.parametrize("url,clean,secrets", [
    ("https://owner:synthetic-proxy-pass@proxy.example/path?token=synthetic-query-secret"
     "#synthetic-fragment-secret", "https://proxy.example/path",
     ("owner:", "synthetic-proxy-pass", "synthetic-query-secret", "synthetic-fragment-secret")),
    (
        "http://owner:synthetic-http-pass@proxy.example:8080/health?key=synthetic-query-secret",
        "http://proxy.example:8080/health",
        ("owner:", "synthetic-http-pass", "synthetic-query-secret"),
    ),
    (
        "postgres://db%40owner:synthetic%3Apg%2Fpass@db.example:5432/store?x=synthetic-query-secret",
        "postgres://db.example:5432/store",
        ("db%40owner", "synthetic%3Apg%2Fpass", "synthetic-query-secret"),
    ),
    (
        "https://own%40er:synthetic%40encoded%3Apass@proxy.example/a%20b?x=synthetic-query-secret",
        "https://proxy.example/a%20b",
        ("own%40er", "synthetic%40encoded%3Apass", "synthetic-query-secret"),
    ),
])
async def test_uri_credentials_are_removed_before_both_bounds_and_every_sink(
    finalization_failure, caplog, side, url, clean, secrets,
):
    kwargs, _, _, restore, events = finalization_failure
    cause = RuntimeError("cause prefix " + url + "\n" + "я" * 5000 + "\n" + url + " cause-tail")
    if side == "original":
        kwargs["runtime"].coordinator.finalize_with_repair.side_effect = cause
    else:
        restore.side_effect = cause
    with pytest.raises(RuntimeError) as raised:
        await agent_finalization.finalize_max_candidate(**kwargs)
    surfaces = [str(raised.value), str(raised.value.__cause__),
                str(raised.value.diagnostics), str(events)]
    surfaces.extend(str(getattr(record, field, "")) for record in caplog.records
                    for field in ("original_error", "rollback_error"))
    assert all(secret not in surface for secret in secrets for surface in surfaces)
    assert clean in str(raised.value)
    assert "cause prefix" in str(raised.value) and "cause-tail" in str(raised.value)
    assert len(str(raised.value).encode()) < 5000


@pytest.mark.parametrize("url", [
    "https://owner:synthetic-truncated-pass@",
    "http://owner:synthetic-truncated-pass",
    "postgres://owner:synthetic-truncated-pass@[bad",
])
def test_unsafe_or_truncated_uri_authority_is_replaced_before_bounding(url):
    safe = bounded_recovery_diagnostic("failure " + url + " final-cause", max_bytes=1500)
    assert "synthetic-truncated-pass" not in safe and "owner:" not in safe
    assert "[REDACTED]" in safe and safe.endswith("final-cause")
