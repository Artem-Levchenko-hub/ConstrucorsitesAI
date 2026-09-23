"""Остановленная генерация не пугает владельца тем, чего не может быть.

23.09.2026 на проде MAX-генерация была законно остановлена: агент написал
миграцию с DROP, договор миграций сработал до публикации. Но к честному отказу
приписалось: «Откат живого превью тоже не удался; среда может показывать
небезопасный черновик до следующей успешной перезагрузки».

Откат «не удался» ровно потому, что живой среды не было: оркестратор ответил
«draft runtime is not running». Среда, которой нет, ничего показывать не может —
предупреждение срабатывает именно тогда, когда оно заведомо ложно. А ячейки
засыпают после каждой сборки, то есть это обычный случай, а не редкий.

Это тот же вид вреда, что и зелёный отчёт о копии без данных: тревога, которая
всегда ложна, учит не читать тревоги вообще. Поэтому здесь закреплено и то, что
ложного предупреждения нет, и то, что настоящее никуда не делось.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.services.generation import agent_verification
from omnia_api.services.generation import runtime as generation_runtime
from omnia_api.services.max_generation_contract import unsafe_max_backend_paths  # noqa: F401

# Точный текст из runtime.py — предупреждение владельцу на английском.
_WARNING = "the runtime may still show"

_BASELINE = {
    "scripts/apply-migrations.mjs": "// platform-owned",
    "src/lib/db/schema.ts": "export const leads = oldLeads;",
    "drizzle/0002_leads.sql": "CREATE TABLE leads ();",
}
# Ровно то, что написал агент в живом прогоне: миграция с удалением.
_UNSAFE = {"drizzle/0003_lead_status.sql": 'ALTER TABLE "leads" DROP COLUMN "note";'}


async def _refuse(monkeypatch: pytest.MonkeyPatch, rollback: AsyncMock) -> ApiError:
    settings = SimpleNamespace(
        agent_gate_max_attempts=0,
        use_agent_gate_feedback=False,
        use_native_agent=False,
        use_sast_gate=False,
    )
    monkeypatch.setattr(agent_verification, "get_settings", lambda: settings)
    monkeypatch.setattr(generation_runtime, "_apply_project_cell_preview_files", rollback)

    with pytest.raises(ApiError) as raised:
        await agent_verification.check_backend_and_normalize_css(
            _active_max_locked_files=frozenset(),
            _agent_res=SimpleNamespace(steps=1),
            _is_edit=True,
            _max_seed_files={},
            baseline=SimpleNamespace(files=_BASELINE),
            files=dict(_UNSAFE),
            ids=SimpleNamespace(project_id=uuid4()),
            project_info=SimpleNamespace(template="max_miniapp", slug="fixture"),
            runtime=SimpleNamespace(handle=None),
            plan=SimpleNamespace(),
            operations=SimpleNamespace(),
        )
    return raised.value


@pytest.mark.asyncio
async def test_a_refusal_without_a_running_runtime_says_nothing_about_the_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Живой случай 23.09: среды нет, значит показывать небезопасное нечему."""
    absent = generation_runtime.PreviewSyncFailed(
        "preview reconciliation failed: Orchestrator rejected request: "
        "draft runtime is not running",
        runtime_absent=True,
    )

    error = await _refuse(monkeypatch, AsyncMock(side_effect=absent))

    assert error.code == "unsafe_generated_backend"
    assert "migration contract" in error.message
    assert _WARNING not in error.message


@pytest.mark.asyncio
async def test_a_refusal_whose_live_rollback_really_failed_still_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Обратная сторона: пока среда работает, а привести её в порядок не вышло,
    # владелец обязан узнать, что она может отдавать небезопасный код.
    real = generation_runtime.PreviewSyncFailed(
        "preview reconciliation failed: package install exited 1",
        runtime_absent=False,
    )

    error = await _refuse(monkeypatch, AsyncMock(side_effect=real))

    assert _WARNING in error.message


@pytest.mark.asyncio
async def test_an_unknown_rollback_failure_keeps_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неизвестный сбой — не повод молчать: молчим только про доказанное."""
    error = await _refuse(monkeypatch, AsyncMock(side_effect=RuntimeError("что-то иное")))

    assert _WARNING in error.message


@pytest.mark.asyncio
async def test_a_successful_rollback_never_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    error = await _refuse(monkeypatch, AsyncMock())

    assert _WARNING not in error.message


def test_the_absent_runtime_is_recognised_by_the_upstream_refusal() -> None:
    """Признак берётся из ответа оркестратора, а не из догадки."""
    from omnia_api.services.orchestrator_client import OrchestratorBadRequest
    from omnia_api.services.project_cell_executor import draft_runtime_absent

    assert draft_runtime_absent(
        OrchestratorBadRequest("draft runtime is not running", status_code=409)
    )
    assert draft_runtime_absent(
        OrchestratorBadRequest("draft runtime is not running", status_code=503)
    )
    assert not draft_runtime_absent(
        OrchestratorBadRequest("workspace state not found", status_code=409)
    )
    assert not draft_runtime_absent(
        OrchestratorBadRequest("draft runtime is not running", status_code=422)
    )
