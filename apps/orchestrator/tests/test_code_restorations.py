import asyncio
import base64
import json
from uuid import UUID

import pytest

from omnia_orchestrator.schemas.code_restoration import (
    CodeRestorationApply,
    CodeRestorationCancel,
    CodeRestorationPrepare,
)


def test_observed_preserves_verified_source_revision():
    from omnia_orchestrator.services.code_restorations import CodeRestorationService

    body = apply_request(request())
    proof = dict(
        candidate_id=str(body.candidate_id),
        source_commit_sha=body.planned_commit_sha,
        fencing_epoch=body.fencing_epoch,
        applied=True,
        source_revision="a" * 64,
    )
    assert CodeRestorationService._observed(proof, body) == proof
    with pytest.raises(RuntimeError, match="revision"):
        CodeRestorationService._observed({**proof, "source_revision": "invalid"}, body)


def request(**changes):
    return CodeRestorationPrepare(
        **{
            "operation_id": UUID(int=1),
            "workspace_id": UUID(int=2),
            "project_id": UUID(int=3),
            "owner_id": UUID(int=4),
            "expected_source_head": "a" * 40,
            "target_commit_sha": "b" * 40,
            "planned_commit_sha": "c" * 40,
            "fencing_epoch": 3,
            "files": [
                {
                    "path": "src/app/page.tsx",
                    "content_base64": base64.b64encode(b"private source").decode(),
                }
            ],
            **changes,
        }
    )


def apply_request(value):
    return CodeRestorationApply(
        **{
            **value.model_dump(exclude={"files", "current_files"}),
            "fencing_epoch": 4,
            "candidate_id": UUID(int=5),
            "report_revision": 1,
            "expected_fencing_epoch": 3,
        }
    )


def cancel_request(value):
    return CodeRestorationCancel(
        **value.model_dump(
            include={
                "operation_id",
                "workspace_id",
                "project_id",
                "owner_id",
            }
        )
    )


class Engine:
    def __init__(self):
        self.calls = []
        self.gate = None
        self.fail_apply = False
        self.fail_cancel = False
        self.observed = None

    async def prepare(self, value):
        self.calls.append("prepare")
        if self.gate:
            await self.gate.wait()
        return {
            "state": "ready",
            "candidate_id": str(UUID(int=5)),
            "private_path": "do-not-expose",
            "files": "private source",
            "report": {
                "revision": 1,
                "mode": "exact",
                "changes": ["Old code"],
                "retained_data": [],
                "unavailable_features": [],
                "warnings": [],
                "blockers": [],
                "next_actions": [],
            },
        }

    async def apply(self, value, prepared):
        self.calls.append("apply")
        self.observed = {
            "candidate_id": str(value.candidate_id),
            "source_commit_sha": value.planned_commit_sha,
            "applied": True,
            "fencing_epoch": value.fencing_epoch,
        }
        if self.fail_apply:
            raise RuntimeError("private password failure")
        return self.observed

    async def observe(self, value, prepared):
        self.calls.append("observe")
        return self.observed

    async def cancel(self, value, prepared):
        self.calls.append("cancel")
        if self.fail_cancel:
            raise RuntimeError("private cleanup error")


def service(tmp_path, engine):
    from omnia_orchestrator.services.code_restorations import CodeRestorationService

    return CodeRestorationService(root=tmp_path, engine=engine)


async def status(svc, value):
    return await svc.get(value.workspace_id, value.operation_id, value.project_id, value.owner_id)


async def test_prepare_durable_retry_and_private_response(tmp_path):
    engine = Engine()
    svc = service(tmp_path, engine)
    value = request()
    accepted = await svc.prepare(value)
    assert accepted["state"] == "preparing"
    await svc.prepare(value)
    await svc.drain()
    ready = await status(service(tmp_path, engine), value)
    assert ready["state"] == "ready" and ready["can_apply"] is True
    assert engine.calls == ["prepare"]
    assert "private" not in json.dumps(ready)
    assert "files" not in json.dumps(ready)


async def test_changed_envelope_or_owner_rejected(tmp_path):
    svc = service(tmp_path, Engine())
    value = request()
    await svc.prepare(value)
    await svc.drain()
    for other in (request(target_commit_sha="d" * 40), request(owner_id=UUID(int=9))):
        with pytest.raises(RuntimeError):
            await svc.prepare(other)
    with pytest.raises(RuntimeError):
        await svc.get(value.workspace_id, value.operation_id, value.project_id, UUID(int=9))


async def test_cancel_before_prepare_is_durable_tombstone(tmp_path):
    engine = Engine()
    value = request()
    svc = service(tmp_path, engine)
    assert (await svc.cancel(cancel_request(value)))["state"] == "cancelled"
    restarted = service(tmp_path, engine)
    assert (await restarted.prepare(value))["state"] == "cancelled"
    await restarted.drain()
    assert engine.calls == []


async def test_cancel_during_prepare_cleans_candidate_before_terminal(tmp_path):
    engine = Engine()
    engine.gate = asyncio.Event()
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await asyncio.sleep(0.02)
    pending = await svc.cancel(cancel_request(value))
    assert pending["state"] != "cancelled"
    engine.gate.set()
    await svc.drain()
    assert engine.calls == ["prepare", "cancel"]
    assert (await status(svc, value))["state"] == "cancelled"


async def test_apply_intent_written_before_effect_and_duplicate_is_not_reapplied(tmp_path):
    engine = Engine()
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    original = engine.apply

    async def observed_apply(body, prepared):
        saved = await status(service(tmp_path, engine), value)
        assert saved["state"] == "applying"
        return await original(body, prepared)

    engine.apply = observed_apply
    body = apply_request(value)
    await svc.apply(body)
    await svc.apply(body)
    await svc.drain()
    done = await status(svc, value)
    assert done["state"] == "completed"
    assert done["observed"]["source_commit_sha"] == "c" * 40
    assert engine.calls.count("apply") == 1
    await svc.apply(body)
    await svc.drain()
    assert engine.calls.count("apply") == 1


async def test_uncertain_effect_restart_observes_without_reapplying(tmp_path):
    engine = Engine()
    engine.fail_apply = True
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    await svc.apply(apply_request(value))
    await svc.drain()
    uncertain = await status(svc, value)
    assert uncertain["state"] == "reconciling"
    assert "password" not in json.dumps(uncertain)
    restarted = service(tmp_path, engine)
    await restarted.recover()
    await restarted.drain()
    assert (await status(restarted, value))["state"] == "completed"
    assert engine.calls == ["prepare", "apply", "observe"]


async def test_unobserved_effect_stays_reconciling(tmp_path):
    engine = Engine()
    engine.fail_apply = True
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    await svc.apply(apply_request(value))
    await svc.drain()
    engine.observed = None
    await svc.recover()
    await svc.drain()
    assert (await status(svc, value))["state"] == "reconciling"
    assert engine.calls.count("apply") == 1


@pytest.mark.parametrize("safe", [True, 1, False, None])
async def test_only_exact_verified_negative_observation_releases_claim(tmp_path, safe):
    engine = Engine()
    engine.fail_apply = True
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    await svc.apply(apply_request(value))
    await svc.drain()
    engine.observed = {
        "candidate_id": str(UUID(int=5)),
        "source_commit_sha": "c" * 40,
        "fencing_epoch": 4,
        "applied": False,
        "safe_to_release": safe,
    }
    await svc.recover()
    await svc.drain()
    result = await status(svc, value)
    if safe is True:
        assert result["state"] == "failed"
        assert result["observed"]["applied"] is False
        assert result["observed"]["safe_to_release"] is True
        assert "прежняя версия" in result["error"]
    else:
        assert result["state"] == "reconciling"
        assert result["observed"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_id": UUID(int=99)},
        {"report_revision": 2},
        {"expected_fencing_epoch": 2},
        {"fencing_epoch": 3},
        {"planned_commit_sha": "d" * 40},
    ],
)
async def test_apply_rejects_changed_candidate_report_or_fence(tmp_path, change):
    engine = Engine()
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    body = apply_request(value).model_copy(update=change)
    with pytest.raises(RuntimeError):
        await svc.apply(body)
    assert engine.calls == ["prepare"]


async def test_cancellation_cleanup_failure_is_not_false_success(tmp_path):
    engine = Engine()
    engine.fail_cancel = True
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    await svc.cancel(cancel_request(value))
    await svc.drain()
    assert (await status(svc, value))["state"] == "reconciling"
    engine.fail_cancel = False
    restarted = service(tmp_path, engine)
    await restarted.recover()
    await restarted.drain()
    assert (await status(restarted, value))["state"] == "cancelled"


async def test_parallel_operation_for_same_workspace_rejected(tmp_path):
    svc = service(tmp_path, Engine())
    await svc.prepare(request())
    await svc.drain()
    with pytest.raises(RuntimeError):
        await svc.prepare(request(operation_id=UUID(int=99)))


async def test_blocked_preparation_can_report_without_a_candidate(tmp_path):
    engine = Engine()
    original = engine.prepare

    async def blocked(value):
        result = await original(value)
        result.update(state="needs_changes", candidate_id=None)
        result["report"]["blockers"] = ["Old code requires an unavailable field"]
        return result

    engine.prepare = blocked
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    result = await status(svc, value)
    assert result["state"] == "needs_changes"
    assert result["candidate_id"] is None
    assert result["can_apply"] is False
    assert result["report"]["blockers"] == ["Old code requires an unavailable field"]


@pytest.mark.parametrize(
    "change",
    [
        {"applied": 1},
        {"source_commit_sha": "d" * 40},
        {"candidate_id": str(UUID(int=99))},
        {"fencing_epoch": 3},
    ],
)
async def test_wrong_activation_receipt_cannot_complete(tmp_path, change):
    engine = Engine()
    original = engine.apply

    async def wrong_receipt(body, prepared):
        return {**await original(body, prepared), **change}

    engine.apply = wrong_receipt
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    await svc.apply(apply_request(value))
    await svc.drain()
    result = await status(svc, value)
    assert result["state"] == "reconciling"
    assert result["observed"] is None


async def test_restart_after_admission_before_execution_does_not_lose_apply(tmp_path):
    engine = Engine()
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    svc._schedule = lambda *_: None  # Process died after durable admission, before task start.
    await svc.apply(apply_request(value))
    assert engine.calls == ["prepare"]
    restarted = service(tmp_path, engine)
    await restarted.recover()
    await restarted.drain()
    assert (await status(restarted, value))["state"] == "completed"
    assert engine.calls == ["prepare", "apply"]


async def test_shutdown_preserves_uncertain_effect_for_observation(tmp_path):
    engine = Engine()
    started = asyncio.Event()

    async def interrupted(body, prepared):
        engine.calls.append("apply")
        started.set()
        await asyncio.Event().wait()

    engine.apply = interrupted
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    await svc.apply(apply_request(value))
    await started.wait()
    await svc.close()
    restarted = service(tmp_path, engine)
    await restarted.recover()
    await restarted.drain()
    assert (await status(restarted, value))["state"] == "reconciling"
    assert engine.calls == ["prepare", "apply", "observe"]


async def test_internal_routes_auth_identity_and_durable_status(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from omnia_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
    from omnia_orchestrator.routers import code_restorations

    svc = service(tmp_path, Engine())
    monkeypatch.setattr(code_restorations, "get_code_restoration_service", lambda: svc)

    def authenticate(token):
        if token != "test-internal-only":
            raise OrchestratorError(code="unauthorized", message="no", status_code=401)

    monkeypatch.setattr(code_restorations, "verify_internal_token", authenticate)
    app = FastAPI()
    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    app.include_router(code_restorations.router)
    value = request()
    path = f"/internal/workspaces/{value.workspace_id}/code-restorations/{value.operation_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(path + "/prepare", json=value.model_dump(mode="json"))
        ).status_code == 401
        client.headers["X-Internal-Token"] = "test-internal-only"
        wrong = request(workspace_id=UUID(int=100))
        assert (
            await client.post(path + "/prepare", json=wrong.model_dump(mode="json"))
        ).status_code == 409
        assert (
            await client.post(path + "/prepare", json=value.model_dump(mode="json"))
        ).status_code == 200
        await svc.drain()
        params = {"project_id": str(value.project_id), "owner_id": str(value.owner_id)}
        assert (await client.get(path, params=params)).json()["state"] == "ready"
        params["owner_id"] = str(UUID(int=99))
        assert (await client.get(path, params=params)).status_code == 409
