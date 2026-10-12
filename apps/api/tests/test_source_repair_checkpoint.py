"""Final checks must not discard later repair source or bypass rollback on storage failure."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_generation_source_check_repair import source_check  # noqa: F401
from yleum_api.services.generation import agent_finalization
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.max_finalization import MaxFinalizationStatus


@pytest.mark.parametrize("storage_fails", [False, True])
async def test_later_finalization_source_is_saved_before_restore(monkeypatch, storage_fails):
    accepted = {"src/app/page.tsx": "accepted"}
    tree = {"src/app/page.tsx": "initial candidate"}
    saved = []
    restored = []

    async def snapshot():
        return dict(tree)

    async def finalize_with_repair(**kwargs):
        tree["src/app/page.tsx"] = "candidate after compiler repair"
        return SimpleNamespace(status=MaxFinalizationStatus.FAILED,
                               redacted_detail="runtime contract still red")

    async def checkpoint(result):
        saved.append(dict(tree))
        if storage_fails:
            raise RuntimeError("private-storage-token")

    async def restore(handle, source):
        restored.append(dict(source))
        tree.clear()
        tree.update(source)

    async def activation_holds(*args):
        return False

    from yleum_api.services import restorations

    monkeypatch.setattr(restorations, "adaptation_activation_holds_generation_lease",
                        activation_holds)
    monkeypatch.setattr(agent_finalization.repo_svc, "read_files", lambda *args: accepted)
    monkeypatch.setattr(agent_finalization, "_restore_project_cell_source", restore)
    runtime = GenerationRuntime(
        handle=SimpleNamespace(snapshot_files=snapshot, prove_restoration_adaptation=None),
        coordinator=SimpleNamespace(finalize_with_repair=finalize_with_repair,
                                    session_factory=None),
    )
    with pytest.raises(RuntimeError, match="MAX_FINALIZATION_FAILED") as raised:
        await agent_finalization.finalize_max_candidate(
            _is_edit=True, _max_has_generated_snapshot=True, _max_shell_enabled=False,
            accumulated="candidate", baseline=SourceBaseline(uuid4(), "a" * 40, accepted),
            files=dict(tree), ids=GenerationIds(*(uuid4() for _ in range(5))), is_free=False,
            prompt_text="Change design", runtime=runtime, plan=SimpleNamespace(),
            operations=SimpleNamespace(checkpoint=checkpoint),
        )
    assert saved == [{"src/app/page.tsx": "candidate after compiler repair"}]
    assert restored == [accepted] and tree == accepted
    assert "private-storage-token" not in str(raised.value)


async def test_native_repair_saves_new_helper_before_provider_failure(source_check):  # noqa: F811
    from yleum_api.services.generation import agent_recovery

    kwargs, tree, _, rollback, mode = source_check
    mode.update(extra=True, provider_error=True)
    saved = []

    async def checkpoint(result):
        saved.append(dict(tree))

    kwargs["operations"].checkpoint = checkpoint
    with pytest.raises(RuntimeError, match="PROVIDER_UNAVAILABLE"):
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert saved and saved[0]["src/lib/new-filter.ts"] == "new candidate helper"
    assert "src/lib/new-filter.ts" not in tree and rollback.await_count == 1


async def test_native_repair_storage_failure_stops_and_still_restores(source_check):  # noqa: F811
    from yleum_api.services.generation import agent_recovery

    kwargs, tree, calls, rollback, mode = source_check
    mode.update(extra=True)

    async def checkpoint(result):
        raise RuntimeError("private-storage-token")

    kwargs["operations"].checkpoint = checkpoint
    with pytest.raises(RuntimeError, match="Не удалось сохранить черновик") as raised:
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert len(calls) == 1 and rollback.await_count == 1
    assert "src/lib/new-filter.ts" not in tree
    assert "private-storage-token" not in str(raised.value)
