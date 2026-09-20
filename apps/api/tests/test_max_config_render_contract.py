"""Characterize three real _process_prompt config/render consumers without AST."""

import builtins
import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from omnia_api.core import config as core_config
from omnia_api.core.config import Settings
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.schemas.max_studio import MaxProjectConfigPayload
from omnia_api.services import (
    agent_native,
    integration_generation,
    max_data_evolution,
    max_project_kit,
    restoration_adaptation,
)
from omnia_api.services.generation import (
    acceptance,
    agent_generation,
    agent_pipeline,
    agent_preparation,
    agent_prompt,
    agent_publication,
    agent_recovery,
    agent_runtime,
    agent_seed,
    agent_verification,
    lifecycle,
    onboarding,
    progress,
)
from omnia_api.services.generation import (
    runtime as generation_runtime,
)


class RenderBoundary(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caller,stored,portable,fallback,fault",
    [
        (caller, stored, portable, "long", None)
        for caller in ["seed", "aborted_rollback", "verification_rollback"]
        for stored in [False, True]
        for portable in [False, True]
    ]
    + [
        (caller, False, False, fallback, None)
        for caller in ["seed", "aborted_rollback", "verification_rollback"]
        for fallback in ["empty", "named"]
    ]
    + [
        (caller, True, False, "long", fault)
        for caller in ["seed", "aborted_rollback", "verification_rollback"]
        for fault in ["read", "validation", "render"]
    ],
)
async def test_real_process_config_render(caller, stored, portable, fallback, fault, monkeypatch):
    pid, uid, mid, rid = (UUID(int=i) for i in range(1, 5))
    state = {"model_ran": False, "runtime_probed": False, "config_reads": 0}
    rendered = []
    prompt = "Создай сервис " + "я" * 1050
    if fallback == "empty":
        prompt = ""
    project = SimpleNamespace(
        template="max_miniapp",
        slug="isolated-config-baseline",
        name="",
        design_preset_id="retail",
        image_gen_enabled=False,
        discovery_spec=None,
        language="ru",
        source="native",
    )
    if fallback == "named":
        project.name = "Named fallback"
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1:1/unused",
        jwt_secret="baseline-only-unused-signing-secret",
    ).model_copy(
        update={
            "use_project_memory": False,
            "use_agentic_builder": True,
            "agentic_builder_canary_users": "",
            "use_design_intelligence_plugin": False,
            "use_design_mood": False,
            "use_skill_injection": False,
            "max_project_shell_enabled": False,
            "use_max_finalization_coordinator": False,
            "use_native_agent": True,
        }
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, model, ident):
            if model is Project:
                return project
            if model is MaxProjectConfig:
                state["config_reads"] += 1
                target = (caller == "seed" and state["config_reads"] == 2) or state["model_ran"]
                if target and fault == "read":
                    raise RuntimeError("baseline-read-failure")
                if target and fault == "validation":
                    return SimpleNamespace(config={"app_name": []})
                if stored:
                    return SimpleNamespace(
                        config=MaxProjectConfigPayload(
                            app_name="Changed after agent"
                            if state["model_ran"]
                            else "Initial config",
                            app_type="custom",
                            summary="Persisted summary",
                        ).model_dump(mode="json")
                    )
            return None

        async def execute(self, stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        async def scalar(self, stmt):
            return 0

        async def commit(self):
            pass

        async def refresh(self, value):
            pass

        def expunge(self, value):
            pass

    def no_network(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    for owner in (
        acceptance,
        agent_generation,
        agent_pipeline,
        agent_preparation,
        agent_prompt,
        agent_publication,
        agent_recovery,
        agent_runtime,
        agent_verification,
        lifecycle,
        onboarding,
    ):
        monkeypatch.setattr(owner, "get_settings", lambda: settings)
    monkeypatch.setattr(core_config, "get_settings", lambda: settings)
    monkeypatch.setattr(lifecycle, "get_engine", lambda: object())
    monkeypatch.setattr(lifecycle, "async_sessionmaker", lambda *a, **k: Session)
    monkeypatch.setattr(
        restoration_adaptation, "append_adaptation_context", AsyncMock(return_value="")
    )
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())
    monkeypatch.setattr(lifecycle, "publish_event", AsyncMock())
    monkeypatch.setattr(
        progress, "append_generation_event", AsyncMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(progress, "generation_event_envelope", lambda event: {})
    monkeypatch.setattr(lifecycle, "set_generation_run_status", AsyncMock())
    monkeypatch.setattr(agent_preparation, "_build_agent_seed_parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_seed, "_apply_project_cell_preview_files", AsyncMock())
    monkeypatch.setattr(agent_recovery, "_apply_project_cell_preview_files", AsyncMock())
    monkeypatch.setattr(lifecycle.stack_routing, "ensure_provisioned", AsyncMock())
    monkeypatch.setattr(integration_generation, "generation_context", AsyncMock(return_value=""))
    monkeypatch.setattr(max_data_evolution, "build_max_agent_guide", AsyncMock(return_value=""))

    product = {"src/app/page.tsx": "export default function Page(){return <main>Service</main>}"}
    handle = (
        SimpleNamespace(
            capabilities={"portable_machine": portable},
            is_portable=lambda: portable,
            snapshot_files=AsyncMock(return_value={}),
            export_files=AsyncMock(return_value=product),
            release=AsyncMock(),
        )
        if portable
        else None
    )
    monkeypatch.setattr(
        agent_runtime,
        "_prepare_max_runtime_context",
        AsyncMock(
            return_value={
                "project_cell_handle": handle,
                "base_agent_executor": AsyncMock(),
                "max_sandbox_capabilities": {},
                "max_sandbox_attested": False,
                "max_shell_enabled": False,
                "active_max_locked_files": frozenset(),
                "agent_result": None,
            }
        ),
    )
    monkeypatch.setattr(agent_runtime, "_project_cell_build", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(
        generation_runtime.orchestrator_client, "agent_build", AsyncMock(return_value={"ok": True})
    )

    async def model(**kwargs):
        state["model_ran"] = True
        return lifecycle.agent_builder.AgentResult(
            done=caller == "verification_rollback",
            summary="Baseline result",
            files=dict(product),
            steps=1,
            stop_reason="done" if caller == "verification_rollback" else "max_steps_red",
        )

    monkeypatch.setattr(agent_native, "run_native_build", model)

    async def runtime_status(*args, **kwargs):
        state["runtime_probed"] = True
        return {"ok": False, "status_code": 500, "error": "baseline-runtime-red"}

    monkeypatch.setattr(agent_runtime, "_project_cell_runtime_check", runtime_status)
    monkeypatch.setattr(generation_runtime.orchestrator_client, "runtime_status", runtime_status)

    real_render = max_project_kit.render_max_starter_files

    def render(config, project_id, *, portable=False):
        target = caller == "seed" or state["model_ran"]
        if target and fault == "render":
            raise RuntimeError("baseline-render-failure")
        files = real_render(config, project_id, portable=portable)
        rendered.append((config.model_dump(mode="json"), project_id, portable, files))
        if target:
            raise RenderBoundary()
        return files

    monkeypatch.setattr(max_project_kit, "render_max_starter_files", render)

    handled = []
    if fault:
        handler_prefix = {
            "seed": "[PP] MAX starter preparation skipped:",
            "aborted_rollback": "[PP] first-MAX safe fallback failed:",
            "verification_rollback": "[PP] native final verification rollback failed:",
        }[caller]
        real_print = builtins.print

        def observe_handler(*args, **kwargs):
            value = " ".join(map(str, args))
            if value.startswith(handler_prefix):
                assert (
                    "validation errors for MaxProjectConfigPayload"
                    if fault == "validation"
                    else f"baseline-{fault}-failure"
                ) in value
                handled.append(handler_prefix)
                raise RenderBoundary()
            real_print(*args, **kwargs)

        monkeypatch.setattr(builtins, "print", observe_handler)

    with pytest.raises(RenderBoundary):
        await lifecycle._process_prompt(
            rid,
            pid,
            uid,
            UUID(int=6),
            mid,
            None,
            prompt,
            "test-model",
            orchestrate=True,
        )
    if fault:
        assert handled == [handler_prefix]
        assert len(rendered) == (0 if caller == "seed" else 1)
        assert state["model_ran"] == (caller != "seed")
        assert state["runtime_probed"] == (caller == "verification_rollback")
        return
    assert len(rendered) == (1 if caller == "seed" else 2)
    assert state["model_ran"] == (caller != "seed")
    assert state["runtime_probed"] == (caller == "verification_rollback")
    assert state["config_reads"] == (2 if caller == "seed" else 3)
    config, actual_pid, actual_portable, files = rendered[-1]
    assert actual_pid == pid and actual_portable is portable
    assert config["app_name"] == (
        ("Initial config" if caller == "seed" else "Changed after agent")
        if stored
        else project.name or "MAX Mini App"
    )
    assert config["summary"] == (
        "Persisted summary" if stored else prompt[:1000] or "Сервис внутри MAX"
    )
    assert "src/app/api/omnia/actions/[id]/route.ts" in files
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    golden = json.loads(
        (Path(__file__).parent / "fixtures/max_config_render_golden.json").read_text(
            encoding="utf-8"
        )
    )
    key = f"{caller}:{stored}:{portable}" + (f":{fallback}" if fallback != "long" else "")
    assert {"files": len(files), "sha256": digest} == golden[key]
    print(f"BASELINE {key} files={len(files)} sha256={digest}")
