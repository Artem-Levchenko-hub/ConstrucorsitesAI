"""Legacy provisioning through the real generation entry point, with external IO faked."""

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from omnia_api.core.config import Settings
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers import messages
from omnia_api.services import restoration_adaptation


class BoundaryReached(BaseException):
    """Stop after the tested consumer, before model/file operations."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,fault",
    [
        (path, fault)
        for path in ["early", "deferred", "deferred_retry"]
        for fault in [None, "start", "ensure", "ready"]
    ]
    + [(path, None) for path in ["blank", "imported", "empty_slug"]],
)
async def test_real_process_provisioning(path, fault, monkeypatch):
    trace, errors, statuses = [], [], []
    original_fault = fault
    deferred = path.startswith("deferred")
    gated = path in {"blank", "imported", "empty_slug"}
    pid, uid, mid, rid, sid = (UUID(int=i) for i in range(1, 6))
    project = SimpleNamespace(
        template="max_miniapp" if deferred else "nextjs_entities",
        slug="isolated-baseline",
        name="Baseline",
        design_preset_id="retail",
        image_gen_enabled=False,
        discovery_spec=None,
        language="ru",
        source="native",
    )
    assistant = SimpleNamespace(tokens_out=None, content="", agent_steps=[])
    if path == "blank":
        project.template = "blank"
    if path == "imported":
        project.source = "imported"
    if path == "empty_slug":
        project.slug = ""

    def no_network(*args, **kwargs):
        raise AssertionError("baseline attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1:1/baseline_unused",
        jwt_secret="baseline-only-unused-signing-secret",
    ).model_copy(
        update={
            "use_project_memory": False,
            "use_agentic_builder": deferred,
            "agentic_builder_canary_users": "",
            "use_design_intelligence_plugin": False,
            "max_project_shell_enabled": False,
            "use_max_finalization_coordinator": False,
        }
    )
    monkeypatch.setattr(messages, "get_settings", lambda: settings)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, model, ident):
            if model is Project:
                return project
            if model is Message:
                return assistant
            if model is Snapshot:
                return SimpleNamespace(commit_sha="baseline-sha")
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

    monkeypatch.setattr(messages, "get_engine", lambda: object())
    monkeypatch.setattr(messages, "async_sessionmaker", lambda *a, **k: Session)
    monkeypatch.setattr(
        restoration_adaptation, "append_adaptation_context", AsyncMock(return_value="")
    )
    monkeypatch.setattr(messages, "clear_stream_state", AsyncMock())
    monkeypatch.setattr(messages, "generation_event_envelope", lambda event: {})

    async def append(session, **kwargs):
        payload = kwargs["payload"]
        if payload.get("action") == "Подготавливаю среду проекта":
            stage = "start"
        elif payload.get("action") == "Среда готова":
            stage = "ready"
        else:
            raise AssertionError(f"unexpected progress {payload!r}")
        trace.append(stage)
        assert payload == {
            "message_id": str(mid),
            "step": None,
            "kind": "step",
            "action": "Подготавливаю среду проекта" if stage == "start" else "Среда готова",
            "tool": "runtime",
            "path": "",
            "ok": True,
            "detail": (
                "Запускаю контейнер и жду готовности перед сборкой."
                if stage == "start"
                else "Контейнер запущен, начинаю сборку приложения."
            ),
        }
        if fault == stage:
            raise RuntimeError(f"baseline-{stage}-failure")
        return SimpleNamespace()

    async def ensure(*args, **kwargs):
        trace.append("ensure")
        assert args == (pid, project.slug, project.template)
        assert kwargs == {"require_ready": True}
        if fault == "ensure":
            raise RuntimeError("baseline-ensure-failure")
        return True

    async def publish(project_id, event_type, payload):
        if event_type == "llm.error":
            errors.append(payload["error"])

    async def status(run_id, state, **kwargs):
        statuses.append((state, kwargs))

    def stop(*args, **kwargs):
        trace.append("boundary")
        raise BoundaryReached()

    async def stop_async(*args, **kwargs):
        stop()

    monkeypatch.setattr(messages, "append_generation_event", append)
    monkeypatch.setattr(messages.stack_routing, "ensure_provisioned", ensure)
    monkeypatch.setattr(messages, "publish_event", publish)
    monkeypatch.setattr(messages, "set_generation_run_status", status)
    # Stop at the next consumer boundary; no model, repository or workspace mutation.
    monkeypatch.setattr(messages.repo_svc, "read_files", stop)
    monkeypatch.setattr(messages, "_build_agent_seed_parts", stop_async)
    monkeypatch.setattr(
        messages.project_cell_executor,
        "maybe_create_project_cell_executor",
        AsyncMock(return_value=None),
    )

    if path == "deferred_retry":
        original_prepare = messages._prepare_max_runtime_context

        # Test-only retry exercises the real closure's flag after a failed await.
        # The ordinary deferred case already calls it twice via prepare and seed.
        async def prepare_with_retry(**kwargs):
            nonlocal fault
            try:
                return await original_prepare(**kwargs)
            except RuntimeError as exc:
                assert str(exc) == f"baseline-{fault}-failure"
                fault = None
                return await original_prepare(**kwargs)

        monkeypatch.setattr(messages, "_prepare_max_runtime_context", prepare_with_retry)

    call = messages._process_prompt(
        rid,
        pid,
        uid,
        UUID(int=6),
        mid,
        None if deferred else sid,
        "Create a test application",
        "test-model",
        orchestrate=True,
    )
    if fault is None or path == "deferred_retry":
        with pytest.raises(BoundaryReached):
            await call
        expected = ["boundary"] if gated else ["start", "ensure", "ready", "boundary"]
        if path == "deferred_retry" and original_fault is not None:
            stages = ["start", "ensure", "ready"]
            expected = stages[: stages.index(original_fault) + 1] + expected
        assert trace == expected
        assert errors == statuses == []
    else:
        await call
        assert (
            trace == ["start", "ensure", "ready"][: ["start", "ensure", "ready"].index(fault) + 1]
        )
        assert errors == [f"baseline-{fault}-failure"]
        assert statuses == [("failed", {"error": f"baseline-{fault}-failure"})]
