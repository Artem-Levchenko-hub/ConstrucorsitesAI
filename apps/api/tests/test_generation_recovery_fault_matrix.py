"""Synthetic crash boundaries use the real attempt/effect transactions, never a provider."""

from dataclasses import replace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_generation_recovery_diagnostic import _armed


@pytest.mark.parametrize("boundary", ["before_store", "after_store"])
async def test_diagnostic_commit_failure_never_reaches_transport(
    db_session,
    test_engine,
    monkeypatch,
    boundary,
):
    import httpx

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts, recovery_execution
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body, _settings = await _armed(db_session, test_engine, monkeypatch)
    posts = []

    class LostCommit(AsyncSession):
        async def commit(self):
            if boundary == "after_store":
                await super().commit()
            raise OSError("synthetic process-boundary acknowledgement loss")

    async def forbidden_post(*args, **kwargs):
        posts.append(True)
        raise AssertionError("uncertain SQL commit dispatched")

    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden_post)
    monkeypatch.setattr(
        provider_attempts,
        "async_sessionmaker",
        lambda *a, **k: async_sessionmaker(test_engine, class_=LostCommit, expire_on_commit=False),
    )
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with provider_effect_scope(cursor), pytest.raises(RuntimeError, match="UNCONFIRMED"):
                await llm_client._chat_post(dict(body), "https://synthetic.invalid", 30)
            assert posts == []
            monkeypatch.setattr(provider_attempts, "async_sessionmaker", async_sessionmaker)
            with provider_effect_scope(replace(cursor, reconcile_only=True)):
                if boundary == "after_store":
                    result = await provider_attempts.prepare_generation_attempt(
                        dict(body),
                        "/v1/chat/completions",
                    )
                    assert result.run_id == run.id
                else:
                    with pytest.raises(RuntimeError, match="UNCONFIRMED"):
                        await provider_attempts.prepare_generation_attempt(
                            dict(body),
                            "/v1/chat/completions",
                        )
            assert posts == []
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == (
        1 if boundary == "after_store" else 0
    )


def test_compose_keeps_activation_off_and_inherits_generation_worker():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load((root / "apps/llm-gateway/deploy/full/docker-compose.yml").read_text())
    expected = {
        "GENERATION_RECOVERY_MODE": "${GENERATION_RECOVERY_MODE:-off}",
        "GENERATION_RECOVERY_EXECUTION_ENABLED": "${GENERATION_RECOVERY_EXECUTION_ENABLED:-false}",
        "GENERATION_RECOVERY_CANARY_PROJECT_IDS": "${GENERATION_RECOVERY_CANARY_PROJECT_IDS:-}",
    }
    for role in ("api", "worker", "generation-worker"):
        environment = compose["services"][role]["environment"]
        assert {key: environment[key] for key in expected} == expected


def test_fault_matrix_points_to_real_tests_without_claiming_live_acceptance():
    import ast
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    matrix = json.loads((root / "tools/qa/generation_recovery_fault_matrix.json").read_text())
    assert [case["scenario"] for case in matrix["cases"]] == list(range(1, 16))
    assert matrix["live_acceptance"] == "NOT_RUN"
    assert matrix["second_max_identity"] == "NOT_RUN_INPUT_UNAVAILABLE"
    for case in matrix["cases"]:
        assert case["nodes"]
        for node in case["nodes"]:
            relative, name = node.split("::")
            module = ast.parse((root / "apps/api" / relative).read_text(encoding="utf-8"))
            assert name in {
                entry.name
                for entry in module.body
                if isinstance(entry, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
