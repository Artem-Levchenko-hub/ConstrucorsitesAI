from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services.generation import agent_seed
from yleum_api.services.generation_failure import public_generation_failure


def seed_call(*, proof=None, legacy=None):
    handle = NS(
        is_portable=lambda: True,
        snapshot_files=AsyncMock(return_value={".omnia/cell.json": '{"version":1,"tasks":[]}'}),
        stage_patch=AsyncMock(),
        sync_preview=AsyncMock(return_value=NS(failure=None, runtime_absent=False)),
    )
    coordinator = NS(fast_check=AsyncMock(return_value=proof)) if proof else None
    runtime = NS(handle=handle, coordinator=coordinator, migration_baseline=None)
    bindings = NS(probe_build=AsyncMock(return_value=legacy))
    emit = AsyncMock()
    call = agent_seed.stage_max_starter(
        _agent_emit=emit, _agent_res=None, _design_contract=None,
        _max_has_generated_snapshot=False,
        _render_current_max_starter_files=AsyncMock(return_value={"package.json": "{}"}),
        bindings=bindings, ids=NS(project_id=uuid4()), orchestrate=True,
        project_info=NS(), runtime=runtime,
    )
    return call, emit, bindings


@pytest.mark.parametrize("dimension", ["bootstrap", "fast_check"])
async def test_failed_core_retains_exact_proof_detail_and_identity(dimension, capsys):
    operation = uuid4()
    detail = (
        "[typecheck]\n.next/types/app/page.ts(2,24): error TS2307: "
        "Cannot find module '../../../src/app/page.js'."
    )
    call, emit, bindings = seed_call(proof=NS(
        outcome="red", dimension=dimension, operation_id=operation,
        redacted_detail=detail,
    ))
    with pytest.raises(ApiError) as raised:
        await call
    stored_error = str(raised.value)
    assert stored_error.startswith("MAX_CORE_PREPARATION_FAILED:")
    assert detail in stored_error
    assert f"dimension={dimension}" in stored_error
    assert f"operation={operation}" in stored_error
    assert detail in emit.await_args_list[-1].args[1]["detail"]
    assert emit.await_args_list[-1].args[1]["ok"] is False
    assert "TS2307" in capsys.readouterr().out
    bindings.probe_build.assert_not_awaited()
    card = public_generation_failure(NS(status="failed", error=stored_error, agent_state={}))
    assert card.code == "runtime_unavailable"
    assert "TS2307" not in card.model_dump_json()
    assert str(operation) not in card.model_dump_json()


async def test_seed_diagnostic_keeps_compiler_failure_after_long_install_and_redacts_secrets(
    capsys,
):
    secret = "synthetic-sensitive-value"
    detail = (
        f"DATABASE_URL=postgresql://qa:{secret}@db.invalid/test\n"
        f"Authorization: Bearer {secret}\n"
        + "[install] successful dependency output\n" * 2500
        + f'{{"API_KEY":"{secret}"}}\n'
        + "[typecheck]\nsrc/app/page.tsx(1,7): error TS2322: "
        "Type 'string' is not assignable to type 'number'.\n"
    )
    call, emit, _ = seed_call(proof=NS(
        outcome="red", dimension="fast_check", operation_id=uuid4(),
        redacted_detail=detail,
    ))
    with pytest.raises(ApiError) as raised:
        await call
    error = str(raised.value)
    failed_step = emit.await_args_list[-1].args[1]["detail"]
    stdout = capsys.readouterr().out
    assert all(secret not in value for value in (error, failed_step, stdout))
    assert all("TS2322" in value for value in (error, failed_step, stdout))
    assert "[REDACTED]" in error
    assert len(error.encode("utf-8")) < 4096
    assert len(failed_step.encode("utf-8")) < 4096


@pytest.mark.parametrize("detail", [None, "", {"untrusted": "not a diagnostic"}])
async def test_legacy_missing_core_diagnostic_has_explicit_fallback(detail):
    call, emit, bindings = seed_call(legacy={"ok": False, "detail": detail})
    with pytest.raises(ApiError) as raised:
        await call
    assert "Check returned no diagnostic." in str(raised.value)
    assert "Check returned no diagnostic." in emit.await_args_list[-1].args[1]["detail"]
    bindings.probe_build.assert_awaited_once()


async def test_legacy_core_diagnostic_is_preserved_without_invented_proof_identity():
    call, emit, bindings = seed_call(legacy={"ok": False, "detail": "[typecheck] exact error"})
    with pytest.raises(ApiError) as raised:
        await call
    error = str(raised.value)
    assert "[typecheck] exact error" in error
    assert "dimension=" not in error and "operation=" not in error
    assert "[typecheck] exact error" in emit.await_args_list[-1].args[1]["detail"]
    bindings.probe_build.assert_awaited_once()
