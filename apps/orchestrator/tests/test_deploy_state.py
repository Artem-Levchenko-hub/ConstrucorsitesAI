"""Durability, restart recovery and idempotency for deployment runs."""

from __future__ import annotations

import pytest

from omnia_orchestrator.services import deploy_state


def test_state_survives_reload(tmp_path, monkeypatch) -> None:
    path = tmp_path / "deploy-runs.json"
    monkeypatch.setenv("OMNIA_DEPLOY_STATE_PATH", str(path))
    deploy_state.reset_for_tests()
    record = deploy_state.start("project-1", idempotency_key="request-1234")
    deploy_state.append_log("project-1", "образ собран")
    deploy_state.update(
        "project-1",
        phase="done",
        prod_url="https://example.test",
        finished_at=deploy_state.now_iso(),
    )

    deploy_state.reset_for_tests()
    restored = deploy_state.get("project-1")
    assert restored is not None
    assert restored.run_id == record.run_id
    assert restored.phase == "done"
    assert restored.logs == ["образ собран"]
    assert restored.can_cancel is False


def test_active_run_becomes_failed_after_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIA_DEPLOY_STATE_PATH", str(tmp_path / "runs.json"))
    deploy_state.reset_for_tests()
    deploy_state.start("project-2")

    deploy_state.reset_for_tests()
    restored = deploy_state.get("project-2")
    assert restored is not None
    assert restored.phase == "failed"
    assert "перезапуском" in str(restored.error)


def test_repeated_idempotency_key_returns_original_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIA_DEPLOY_STATE_PATH", str(tmp_path / "runs.json"))
    deploy_state.reset_for_tests()
    first = deploy_state.start("project-3", idempotency_key="same-request")
    deploy_state.update("project-3", phase="done")
    repeated = deploy_state.start("project-3", idempotency_key="same-request")
    assert repeated is first
    assert repeated.phase == "done"


def test_idempotency_key_cannot_replay_another_revision(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMNIA_DEPLOY_STATE_PATH", str(tmp_path / "runs.json"))
    deploy_state.reset_for_tests()
    deploy_state.start(
        "project-4",
        idempotency_key="same-request",
        commit_sha="a" * 40,
    )
    deploy_state.update("project-4", phase="done")

    with pytest.raises(deploy_state.DeployRevisionConflict):
        deploy_state.start(
            "project-4",
            idempotency_key="same-request",
            commit_sha="b" * 40,
        )
