"""Restored owned product source is attributable without claiming a new write."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import agent_generation, source_checkpoint
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline

ENTRY = "src/app/page.tsx"
LOCKED = "src/lib/omnia/catalog-client.ts"
STARTER = {
    ENTRY: "export default function Page(){return <main>Starter</main>}",
    LOCKED: "owned SDK",
}
PRODUCT = {**STARTER, ENTRY: "export default function Page(){return <main>Saved catalog</main>}"}


async def checkpoint_context(monkeypatch, ids, baseline, tree, *, corruption=None):
    """Real codec and namespace/digest loader; isolated immutable object bytes."""
    source_run = uuid4()
    data = source_checkpoint.encode_checkpoint(
        project_id=ids.project_id,
        user_id=ids.user_id,
        run_id=source_run,
        baseline_sha=baseline.sha,
        files=source_checkpoint.checkpoint_sources(tree),
        deleted_paths=[],
    )
    if corruption in {"foreign_project", "foreign_owner", "foreign_run", "locked_path"}:
        payload = json.loads(data)
        if corruption == "locked_path":
            payload["files"][LOCKED] = "forged SDK"
        else:
            key = {
                "foreign_project": "project_id",
                "foreign_owner": "user_id",
                "foreign_run": "run_id",
            }[corruption]
            payload[key] = str(uuid4())
        data = json.dumps(payload).encode()
    digest = hashlib.sha256(data).hexdigest()
    if corruption == "digest":
        digest = "0" * 64
    monkeypatch.setattr(source_checkpoint, "_get_checkpoint", lambda *_args: data)
    loaded = await source_checkpoint.load_checkpoint_object(
        ids.project_id,
        source_run,
        object_key=f"generation-checkpoints/{ids.project_id}/{source_run}/{digest}.json",
        digest=digest,
    )
    decoded = (
        None
        if loaded is None
        else source_checkpoint.decode_checkpoint(
            loaded,
            project_id=ids.project_id,
            user_id=ids.user_id,
            run_id=source_run,
            baseline_sha="b" * 40 if corruption == "baseline" else baseline.sha,
        )
    )
    if decoded is None:
        return None
    # The real pipeline overlays only safe decoded files onto its owned seed.
    current = {**baseline.files, **decoded["files"]}
    for path in decoded["deleted_paths"]:
        current.pop(path, None)
    return current


async def run_readonly_turn(
    monkeypatch,
    *,
    tree=PRODUCT,
    corruption=None,
    is_edit=False,
    tamper=None,
    persist_fails=False,
    carrier=True,
    seed=None,
    done=True,
):
    from yleum_api.services import agent_builder, max_behavior_proof, max_generation_contract

    ids = GenerationIds(*(uuid4() for _ in range(5)))
    baseline = SourceBaseline(uuid4(), "a" * 40, dict(STARTER))
    fixed_seed = dict(STARTER if seed is None else seed)
    restored = await checkpoint_context(monkeypatch, ids, baseline, tree, corruption=corruption)
    if corruption is not None:
        assert restored is None
    workspace = dict(tree)
    events, saves, provider_calls = [], [], []
    settings = get_settings().model_copy(
        update={"use_native_agent": False, "agent_max_segments": 1}
    )
    monkeypatch.setattr(agent_generation, "get_settings", lambda: settings)
    monkeypatch.setattr(max_behavior_proof, "freeze_for_turn", AsyncMock())
    monkeypatch.setattr(
        max_generation_contract, "max_source_completion_gap", lambda *_a, **_k: None
    )

    async def snapshot():
        return dict(workspace)

    async def provider(**kwargs):
        provider_calls.append(kwargs)
        if tamper:
            workspace.update(tamper)
        return AgentResult(
            done=done,
            summary="saved source is ready",
            files={},
            steps=1,
            stop_reason="done" if done else "exploring",
            needs_finalization=done,
        )

    monkeypatch.setattr(agent_builder, "run_agent_build", provider)

    async def checkpoint(result):
        saves.append(dict(result.files))
        if persist_fails:
            raise RuntimeError("private checkpoint storage unavailable")

    async def emit(name, payload):
        events.append((name, payload))

    runtime = GenerationRuntime(
        handle=SimpleNamespace(
            export_files=AsyncMock(return_value={}),
            snapshot_files=snapshot,
            is_portable=lambda: True,
            capabilities={"portable_machine": True},
            prove_restoration_adaptation=None,
        ),
        coordinator=SimpleNamespace(source_edit_deadline=AsyncMock(return_value=None)),
        migration_baseline=fixed_seed,
    )
    operations = SimpleNamespace(
        execute=AsyncMock(return_value={"ok": True}), emit=emit, snapshot_files=snapshot
    )
    result, failure = await agent_generation.execute_agent_turn(
        _agent_res=None,
        _is_edit=is_edit,
        _max_has_generated_snapshot=False,
        _max_seed_files=fixed_seed,
        _max_shell_enabled=False,
        baseline=baseline,
        ids=ids,
        is_free=False,
        project_info=SimpleNamespace(template="max_miniapp"),
        prompt_text="Change title" if is_edit else "Build product",
        runtime=runtime,
        plan=SimpleNamespace(
            system="fixture",
            user="build",
            model="fixture",
            escalate_model=None,
            steps=2,
            bare_stack=False,
            stack_guide="",
            stack_system="fixture",
            skills=None,
            seed_context="",
        ),
        operations=operations,
        source_checkpoint=checkpoint,
        workspace_context_files=restored if carrier else None,
    )
    return result, failure, events, saves, provider_calls, runtime, operations, workspace


@pytest.mark.asyncio
async def test_owned_valid_product_checkpoint_needs_no_new_model_write(monkeypatch):
    result, failure, events, saves, calls, runtime, operations, workspace = await run_readonly_turn(
        monkeypatch
    )
    assert result.done and result.needs_finalization and result.stop_reason == "done"
    assert result.files == {} and result.evidence == {}  # No fabricated write/proof.
    assert failure is None and saves == [PRODUCT] and len(calls) == 1
    assert calls[0]["bounded_repair"] is True
    assert not any(payload.get("action") == "no_ai_write" for _, payload in events)
    _same, final_files, _steps = await agent_generation.complete_empty_legacy_build(
        _agent_res=result,
        _is_edit=False,
        _max_seed_files=STARTER,
        ids=None,
        runtime=runtime,
        plan=None,
        operations=operations,
    )
    assert final_files == workspace == PRODUCT
    assert (
        result.files == {} and len(calls) == 1
    )  # Full finalization tree; no paid write-floor retry.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tree",
    [
        STARTER,
        {**STARTER, "README.md": "Resume product complete"},
        {**STARTER, "next-env.d.ts": "generated compiler bookkeeping"},
        {**STARTER, "tsconfig.tsbuildinfo": "generated compiler cache"},
        {**STARTER, ".omnia/design.json": "platform metadata"},
        {**STARTER, "src/app/globals.css": "body {color: blue}"},
    ],
)
async def test_owned_but_untouched_or_non_product_checkpoint_still_denies(monkeypatch, tree):
    result, failure, events, *_ = await run_readonly_turn(monkeypatch, tree=tree)
    assert not result.done and result.stop_reason == "no_ai_write" and result.files == {}
    assert failure is None
    assert any(payload.get("action") == "no_ai_write" for _, payload in events)
    assert "Google" not in result.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["foreign_project", "foreign_owner", "foreign_run", "baseline", "digest", "locked_path"],
)
async def test_unvalidated_foreign_stale_or_tampered_checkpoint_never_gives_credit(
    monkeypatch, corruption
):
    result, *_ = await run_readonly_turn(monkeypatch, corruption=corruption)
    assert not result.done and result.stop_reason == "no_ai_write"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        {ENTRY: "export default function Page(){return <main>Different source</main>}"},
        {LOCKED: "changed protected SDK"},
    ],
)
async def test_later_owned_current_tree_tamper_cannot_borrow_saved_delta(monkeypatch, tamper):
    result, *_ = await run_readonly_turn(monkeypatch, tamper=tamper)
    assert not result.done and result.stop_reason == "no_ai_write"


@pytest.mark.asyncio
async def test_physical_product_without_validated_adjacent_carrier_still_denies(monkeypatch):
    result, *_ = await run_readonly_turn(monkeypatch, carrier=False)
    assert not result.done and result.stop_reason == "no_ai_write"


@pytest.mark.asyncio
async def test_failed_checkpoint_save_cannot_authorize_restored_source(monkeypatch):
    result, failure, _events, _saves, calls, *_ = await run_readonly_turn(
        monkeypatch, persist_fails=True
    )
    assert not result.done and result.stop_reason == "checkpoint_error" and failure is None
    assert calls == []


@pytest.mark.asyncio
async def test_existing_product_edit_still_requires_change_against_accepted_source(monkeypatch):
    with pytest.raises(RuntimeError, match="edit produced no source changes"):
        await run_readonly_turn(monkeypatch, tree=STARTER, is_edit=True)


def test_matching_physical_tamper_cannot_replace_fixed_protected_reference():
    tampered = {**PRODUCT, LOCKED: "unattributable protected change"}
    assert not agent_generation._resumed_product_delta(STARTER, tampered, tampered)


@pytest.mark.asyncio
async def test_attribution_does_not_promote_a_stopped_agent_result(monkeypatch):
    result, failure, events, _saves, calls, *_ = await run_readonly_turn(monkeypatch, done=False)
    assert not result.done and not result.needs_finalization and result.stop_reason == "exploring"
    assert result.files == {} and result.evidence == {} and failure is None and len(calls) == 1
    assert not any(payload.get("action") == "no_ai_write" for _, payload in events)
