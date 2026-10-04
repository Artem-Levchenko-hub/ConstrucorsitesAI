"""Characterize three real _process_prompt config/render consumers without AST."""

import builtins
import hashlib
import json
import re
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from yleum_api.core import config as core_config
from yleum_api.core.config import Settings
from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.models.project import Project
from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services import (
    agent_native,
    integration_generation,
    max_data_evolution,
    max_project_kit,
    restoration_adaptation,
)
from yleum_api.services.generation import (
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


class RenderBoundary(BaseException):
    pass


_LEGACY_DEPENDENCY_HASHES = {
    "package.json": "4cc5e3cd91a3e897d1f50a73b5e0d6599c4458ec1ff5933be1cc7b090ec6e80b",
    "pnpm-lock.yaml": "a91a9ccf05cbdcb8876256b780d24060b93f00c55f39557626b764d6b7866d94",
}


_LEGACY_PREVIEW_HASHES = {
    "src/app/api/max/session/route.ts": (
        "e7fa8b1280e174af0a31586a0cd81008dc3b42dd94bbad9999533f7d3a4161a3"
    ),
    "src/app/api/omnia/preview-session/route.ts": (
        "2b0413098b44e778c2d317d46895b7d4b5e9452a3a819dd733e8de32148ff507"
    ),
    "src/components/MaxAppProvider.tsx": (
        "c0cc8a4e4dd384e4074afc8b41e0ae70395264f28e3ae2841ab0ac37e2500333"
    ),
}


_LEGACY_ANALYTICS_HASHES = {
    "src/app/api/max/session/route.ts": (
        "8574fb957de0f9e98b7a81832f139670282eac029f9a643b361ea97e380badc7"
    ),
    "src/app/api/omnia/actions/route.ts": (
        "7a367d6f19e6bc53e1c6ca28b30c9c335f894f36a7d716af5479196103e83dda"
    ),
    "src/app/api/omnia/events/route.ts": (
        "28b7c7c84d10335b7f7bee5ba4726c362b24da2b5c7d050d39bd1e8b3de7341a"
    ),
}


def _normalize_analytics(files):
    fixtures = Path(__file__).parents[2] / "orchestrator/tests/fixtures"
    overrides = json.loads(
        (fixtures / "max_template_analytics_overrides.json").read_text(encoding="utf-8")
    )
    legacy = json.loads(
        (Path(__file__).parent / "fixtures/max_config_render_legacy_analytics.json").read_text(
            encoding="utf-8"
        )
    )
    added = "src/lib/omnia/analytics.ts"
    assert legacy["revision"] == "20382350beccf2da361db1f6c9e9425a91b9267c"
    assert set(overrides) == set(_LEGACY_ANALYTICS_HASHES) | {added}
    assert set(legacy["files"]) == set(_LEGACY_ANALYTICS_HASHES)
    normalized = dict(files)
    for path, entry in overrides.items():
        actual = files[path]
        if path == added:
            match = re.search(r'^  const project = ("[^"\n]*");$', actual, re.M)
            assert match is not None
            identity = json.loads(match.group(1))
            assert identity == "" or str(UUID(identity)) == identity
            actual = actual.replace(
                match.group(0), '  const project = process.env.OMNIA_PROJECT_ID || "";', 1
            )
        assert hashlib.sha256(actual.encode()).hexdigest() == entry["sha256"], path
        if path == added:
            normalized.pop(path)
        else:
            baseline = legacy["files"][path]["source"]
            assert hashlib.sha256(baseline.encode()).hexdigest() == _LEGACY_ANALYTICS_HASHES[path]
            normalized[path] = baseline
    return normalized


def _normalize_preview_renewal(files):
    fixtures = Path(__file__).parents[2] / "orchestrator/tests/fixtures"
    overrides = json.loads(
        (fixtures / "max_template_preview_renewal_overrides.json").read_text(encoding="utf-8")
    )
    added = "src/lib/max/owner-preview-renewal.ts"
    assert set(overrides) == set(_LEGACY_PREVIEW_HASHES) | {added}
    legacy = json.loads(
        (
            Path(__file__).parent / "fixtures/max_config_render_legacy_preview_renewal.json"
        ).read_text(encoding="utf-8")
    )
    assert legacy["revision"] == "de9800fc2dccaf614cb9ca97c530629f3f0485c2"
    assert set(legacy["files"]) == set(_LEGACY_PREVIEW_HASHES)
    normalized = dict(files)
    preview_path = "src/app/api/omnia/preview-session/route.ts"
    sentinel = 'const MANAGED_PROJECT_ID: string = "__OMNIA_PROJECT_ID__";'
    for path, entry in overrides.items():
        actual = files[path]
        declaration = None
        if path == preview_path:
            match = re.search(r'^const MANAGED_PROJECT_ID: string = ("[^"\n]*");$', actual, re.M)
            assert match is not None
            literal = match.group(1)
            identity = json.loads(literal)
            assert identity == "" or str(UUID(identity)) == identity
            declaration = match.group(0)
            actual = actual.replace(declaration, sentinel, 1)
        assert hashlib.sha256(actual.encode("utf-8")).hexdigest() == entry["sha256"], path
        if path == added:
            normalized.pop(path)
        else:
            baseline = legacy["files"][path]["source"]
            assert (
                hashlib.sha256(baseline.encode("utf-8")).hexdigest()
                == (_LEGACY_PREVIEW_HASHES[path])
            ), path
            normalized[path] = (
                baseline.replace(sentinel, declaration, 1) if declaration else baseline
            )
    return normalized


def _assert_render_golden(files, expected):
    """Check reviewed changed files, then the unchanged original full SDK golden."""
    fixture_root = Path(__file__).parent / "fixtures"
    legacy = json.loads(
        (fixture_root / "max_config_render_legacy_dependencies.json").read_text(encoding="utf-8")
    )
    assert legacy["revision"] == "49b071162a5017374b96a193c23b9d48e4cf0995"
    assert set(legacy["files"]) == set(_LEGACY_DEPENDENCY_HASHES)
    overrides_path = (
        Path(__file__).parents[2]
        / "orchestrator/tests/fixtures/max_template_dependency_overrides.json"
    )
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    # Project only these two verified dependency blobs back to the old baseline;
    # all other rendered bytes must still match the unchanged original golden.
    original_dependencies = _normalize_preview_renewal(_normalize_analytics(files))
    for path, frozen_hash in _LEGACY_DEPENDENCY_HASHES.items():
        assert hashlib.sha256(files[path].encode()).hexdigest() == overrides[path]["sha256"], path
        baseline = legacy["files"][path]
        assert hashlib.sha256(baseline.encode()).hexdigest() == frozen_hash, path
        original_dependencies[path] = baseline
    support_path = "src/app/support/page.tsx"
    support_override = json.loads(
        (overrides_path.parent / "max_template_support_overrides.json").read_text(encoding="utf-8")
    )
    assert set(support_override) == {support_path}
    assert (
        hashlib.sha256(files[support_path].encode()).hexdigest()
        == (support_override[support_path]["sha256"])
    ), support_path
    legacy_support = json.loads(
        (fixture_root / "max_config_render_legacy_support.json").read_text(encoding="utf-8")
    )
    assert legacy_support["revision"] == "65322513e1a4eeee42a9195bb5747d14179928e1"
    assert legacy_support["path"] == support_path
    assert hashlib.sha256(legacy_support["source"].encode()).hexdigest() == (
        "c532e2fd9be3327afefa8f380ad7cdbe18231362b499241a75e785e8f55f5706"
    )
    original_dependencies[support_path] = legacy_support["source"]
    digest = hashlib.sha256(
        json.dumps(original_dependencies, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert {"files": len(original_dependencies), "sha256": digest} == expected
    return digest


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
    # Snapshot is the full staged tree, including seed; export is only the model diff.
    workspace_files: dict[str, str] = {}
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

    async def apply_files(*, files, **kwargs):
        for path, content in files.items():
            if content == "":
                workspace_files.pop(path, None)
            else:
                workspace_files[path] = content

    monkeypatch.setattr(agent_seed, "_apply_project_cell_preview_files", apply_files)
    monkeypatch.setattr(agent_recovery, "_apply_project_cell_preview_files", apply_files)
    monkeypatch.setattr(integration_generation, "generation_context", AsyncMock(return_value=""))
    monkeypatch.setattr(max_data_evolution, "build_max_agent_guide", AsyncMock(return_value=""))

    product = {"src/app/page.tsx": "export default function Page(){return <main>Service</main>}"}
    # Ячейка есть у КАЖДОГО проекта — запасного контейнера больше нет, и
    # «не переносимая» ячейка отличается от переносимой только возможностями.
    handle = SimpleNamespace(
        capabilities={"portable_machine": portable},
        is_portable=lambda: portable,
        snapshot_files=AsyncMock(side_effect=lambda: dict(workspace_files)),
        export_files=AsyncMock(return_value=product),
        release=AsyncMock(),
    )
    monkeypatch.setattr(
        agent_runtime,
        "_prepare_max_runtime_context",
        AsyncMock(
            return_value={
                "project_cell_handle": handle,
                "base_agent_executor": AsyncMock(),
                "max_shell_enabled": False,
                "active_max_locked_files": frozenset(),
                "agent_result": None,
            }
        ),
    )
    monkeypatch.setattr(agent_runtime, "_project_cell_build", AsyncMock(return_value={"ok": True}))

    async def model(**kwargs):
        state["model_ran"] = True
        workspace_files.update(product)
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
    golden = json.loads(
        (Path(__file__).parent / "fixtures/max_config_render_golden.json").read_text(
            encoding="utf-8"
        )
    )
    key = f"{caller}:{stored}:{portable}" + (f":{fallback}" if fallback != "long" else "")
    digest = _assert_render_golden(files, golden[key])
    print(f"BASELINE {key} files={len(files)} sha256={digest}")


@pytest.mark.parametrize(
    "changed",
    [
        "src/lib/max/session.ts",
        "package.json",
        "pnpm-lock.yaml",
        "src/app/support/page.tsx",
        "src/lib/max/owner-preview-renewal.ts",
        "src/components/MaxAppProvider.tsx",
        "src/app/api/max/session/route.ts",
        "src/app/api/omnia/preview-session/route.ts",
        "src/app/api/omnia/actions/route.ts",
        "src/app/api/omnia/events/route.ts",
        "src/lib/omnia/analytics.ts",
    ],
)
def test_config_render_golden_rejects_sdk_or_unreviewed_dependency_drift(changed):
    config = MaxProjectConfigPayload(
        app_name="Initial config",
        app_type="custom",
        summary="Persisted summary",
    )
    files = max_project_kit.render_max_starter_files(config, UUID(int=1))
    golden = json.loads(
        (Path(__file__).parent / "fixtures/max_config_render_golden.json").read_text(
            encoding="utf-8"
        )
    )
    _assert_render_golden(files, golden["seed:True:False"])
    if changed == "package.json":
        package = json.loads(files[changed])
        package["dependencies"]["unexpected-dependency"] = "1.0.0"
        files[changed] = json.dumps(package, indent=2) + "\n"
    elif changed == "pnpm-lock.yaml":
        files[changed] += "\n# Unexpected lockfile drift\n"
    else:
        files[changed] += "\n// Unexpected SDK drift\n"
    with pytest.raises(AssertionError):
        _assert_render_golden(files, golden["seed:True:False"])
