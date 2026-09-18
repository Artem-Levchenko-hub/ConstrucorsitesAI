"""P01: one long «building» becomes durable substages with a heartbeat, byte
progress, first-class metrics and fixed reason codes — recorded in the
publication journal, readable after a restart, never leaking raw errors."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.schemas.runtime import DeployResponse
from omnia_orchestrator.services.cell_publication import CellPublicationService
from omnia_orchestrator.services.machine_environment import MachineEnvironmentStore
from omnia_orchestrator.services.publication_trace import (
    FORMAT_VERSION,
    PublicationTrace,
    reason_code,
)
from tests.test_cell_publication import request
from tests.test_machine_environment import ArchiveBackend

RELEASE = {"prod_url": "https://app.example.test", "image_id": "sha256:" + "a" * 64}


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += seconds


def _service(tmp_path, value, run_id):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "history": [
                {
                    "idempotency_key": value.idempotency_key,
                    "request_digest": "digest",
                    "response": {
                        "project_id": str(value.project_id),
                        "run_id": run_id,
                        "phase": "queued",
                    },
                }
            ],
        },
    )
    return service


def _response(service, value):
    return service._read(value.project_id)["history"][-1]["response"]


def test_stages_are_recorded_in_order_with_elapsed_bytes_and_metrics():
    clock = Clock()
    stamps = iter(f"2026-09-18T00:00:{i:02d}+00:00" for i in range(60))
    trace = PublicationTrace(clock=clock, now=lambda: next(stamps))
    trace.stage("preflight")
    clock.tick(1.5)
    trace.stage("capture_volumes")
    trace.add_bytes(1000)
    trace.add_bytes(24)
    clock.tick(2)
    trace.stage("verify_artifacts", bytes_total=1024)
    trace.add_bytes(1024)
    clock.tick(0.5)
    live = trace.snapshot()
    assert live["stage"] == "verify_artifacts"
    assert live["progress"] == {"bytes_done": 1024, "bytes_total": 1024, "files_done": None}
    trace.end_stage()

    snapshot = trace.snapshot()
    assert snapshot["format_version"] == FORMAT_VERSION
    assert snapshot["stage"] is None and snapshot["progress"] is None
    assert [item["stage"] for item in snapshot["stages"]] == [
        "preflight",
        "capture_volumes",
        "verify_artifacts",
    ]
    assert [item["elapsed_ms"] for item in snapshot["stages"]] == [1500, 2000, 500]
    # Export volume: total unknown while it runs, never invented.
    assert snapshot["stages"][1]["bytes_done"] == 1024
    assert snapshot["stages"][1]["bytes_total"] is None
    assert snapshot["metrics"] == {
        "preflight_ms": 1500,
        "capture_volumes_ms": 2000,
        "verify_artifacts_ms": 500,
    }
    # A repeated stage (first publish starts the app twice) accumulates.
    trace.stage("preflight")
    clock.tick(1)
    trace.end_stage()
    assert trace.metrics["preflight_ms"] == 2500
    with pytest.raises(ValueError):
        trace.stage("not-a-stage")


def test_reason_codes_are_fixed_and_never_raw_text():
    assert reason_code(asyncio.CancelledError()) == "cancelled"
    exhausted = TimeoutError("machine operation work budget exhausted")
    assert reason_code(exhausted) == "deadline_exceeded"
    assert reason_code(CellResourceError("public HTTPS activation failed")) == "tls_failed"
    assert reason_code(CellResourceError("public product service readiness failed")) == (
        "service_readiness_failed"
    )
    assert reason_code(CellIdentityConflict("publication source revision changed")) == (
        "source_changed"
    )
    assert reason_code(CellIdentityConflict("something else")) == "identity_conflict"
    assert reason_code(RuntimeError("docker: password=hunter2 rejected")) == "internal_error"


async def test_successful_run_writes_stages_heartbeat_and_first_class_metrics(tmp_path):
    value = request()
    run_id = str(UUID(int=25))
    service = _service(tmp_path, value, run_id)

    async def prepare(_request, _run_id, trace):
        trace.stage("preflight")
        trace.stage("capture_volumes")
        trace.add_bytes(10)
        assert _response(service, value)["stage"] == "capture_volumes"  # flushed on change
        return dict(RELEASE)

    async def activate(_request, _release, trace):
        trace.stage("activate")
        trace.stage("observe")

    service._prepare = prepare
    service._activate = activate
    await service._execute(value, run_id)

    result = service.get(value.project_id)
    assert result is not None and result.phase == "done"
    assert result.format_version == FORMAT_VERSION
    assert [item.stage for item in result.stages] == [
        "preflight",
        "capture_volumes",
        "activate",
        "observe",
    ]
    assert result.stages[1].bytes_done == 10
    assert result.stage is None and result.progress is None and result.heartbeat_at
    assert {"prepare_ms", "activate_ms", "total_ms", "capture_volumes_ms"} <= set(result.metrics)
    assert result.metrics["total_ms"] >= result.metrics["prepare_ms"]
    # The old wire stays exactly as it was.
    assert [entry.split("=", 1)[0] for entry in result.logs] == [
        "prepare_ms",
        "activate_ms",
        "total_ms",
    ]
    # Durable: a fresh controller reads the same stages back.
    again = CellPublicationService(SimpleNamespace(), root=tmp_path).get(value.project_id)
    assert again is not None and [item.stage for item in again.stages] == [
        item.stage for item in result.stages
    ]


async def test_failure_keeps_stage_metrics_and_reason_without_leaking_the_error(tmp_path):
    value = request()
    run_id = str(UUID(int=26))
    service = _service(tmp_path, value, run_id)

    async def prepare(_request, _run_id, trace):
        trace.stage("preflight")
        trace.stage("seed_data")
        raise RuntimeError("docker: password=hunter2 rejected")

    service._prepare = prepare
    await service._execute(value, run_id)

    result = service.get(value.project_id)
    assert result is not None and result.phase == "failed"
    assert result.error_stage == "seed_data" and result.reason_code == "internal_error"
    assert [item.stage for item in result.stages] == ["preflight", "seed_data"]
    assert {"preflight_ms", "seed_data_ms", "total_ms"} <= set(result.metrics)
    journal = (tmp_path / str(value.project_id) / "publication.json").read_text()
    assert "hunter2" not in journal and "password" not in journal


async def test_known_failures_get_their_reason_code(tmp_path):
    value = request()
    run_id = str(UUID(int=27))
    service = _service(tmp_path, value, run_id)

    async def prepare(_request, _run_id, trace):
        trace.stage("capture_volumes")
        return dict(RELEASE)

    async def activate(_request, _release, trace):
        trace.stage("tls")
        raise CellResourceError("public HTTPS activation failed")

    service._prepare = prepare
    service._activate = activate
    await service._execute(value, run_id)
    result = service.get(value.project_id)
    assert result is not None and result.phase == "failed"
    assert (result.error_stage, result.reason_code) == ("tls", "tls_failed")
    assert "prepare_ms" in result.metrics and "tls_ms" in result.metrics


async def test_deadline_and_cancellation_are_reported_honestly(tmp_path):
    value = request()
    run_id = str(UUID(int=28))
    service = _service(tmp_path, value, run_id)

    async def slow(_request, _run_id, trace):
        trace.stage("verify_artifacts", bytes_total=100)
        raise TimeoutError("machine operation work budget exhausted")

    service._prepare = slow
    await service._execute(value, run_id)
    result = service.get(value.project_id)
    assert result is not None and result.phase == "failed"
    assert (result.error_stage, result.reason_code) == ("verify_artifacts", "deadline_exceeded")

    run_id = str(UUID(int=29))
    service = _service(tmp_path, value, run_id)

    async def cancelled(_request, _run_id, trace):
        trace.stage("preflight")
        raise asyncio.CancelledError()

    service._prepare = cancelled
    with pytest.raises(asyncio.CancelledError):
        await service._execute(value, run_id)
    result = service.get(value.project_id)
    assert result is not None and result.phase == "failed"
    assert (result.error_stage, result.reason_code) == ("preflight", "cancelled")


async def test_heartbeat_advances_while_a_stage_makes_no_visible_progress(tmp_path):
    value = request()
    run_id = str(UUID(int=30))
    service = _service(tmp_path, value, run_id)
    service.heartbeat_seconds = 0.02
    seen: list[str | None] = []

    async def prepare(_request, _run_id, trace):
        trace.stage("capture_volumes")
        for _ in range(6):
            await asyncio.sleep(0.06)
            seen.append(_response(service, value).get("heartbeat_at"))
        return dict(RELEASE)

    async def activate(*_args):
        return None

    service._prepare = prepare
    service._activate = activate
    await service._execute(value, run_id)
    assert len({stamp for stamp in seen if stamp}) >= 2
    assert service.get(value.project_id).phase == "done"


async def test_heartbeat_never_rewrites_a_finished_run(tmp_path):
    value = request()
    run_id = str(UUID(int=31))
    service = _service(tmp_path, value, run_id)
    trace = PublicationTrace()
    trace.stage("preflight")
    service._phase(value.project_id, run_id, "done", finished_at="2026-09-18T00:00:00+00:00")
    service._touch(value.project_id, run_id, trace)
    response = _response(service, value)
    assert response["phase"] == "done" and "stage" not in response


def test_environment_store_reports_capture_stages_and_bytes(tmp_path):
    backend = ArchiveBackend()
    store = MachineEnvironmentStore(tmp_path, uuid4(), backend, max_bytes=4096)
    trace = PublicationTrace()
    store.observer = trace

    reference = store.capture(
        manifest_digest="b" * 64, base_image="sha256:" + "c" * 64, volumes=("repo", "home")
    )

    finished = [item["stage"] for item in trace.snapshot()["stages"]]
    assert finished == ["capture_rootfs"] and trace.current_stage() == "capture_volumes"
    exported = sum(len(item) for item in backend.volumes.values())
    assert trace.snapshot()["progress"] == {
        "bytes_done": exported,
        "bytes_total": None,
        "files_done": None,
    }
    total = reference.size + sum(volume.size for volume in reference.volumes)
    trace.stage("verify_artifacts", bytes_total=total)
    store.validate(reference, manifest_digest="b" * 64)
    assert trace.snapshot()["progress"] == {
        "bytes_done": total,
        "bytes_total": total,
        "files_done": None,
    }


def test_old_responses_read_as_format_1_without_inventing_progress():
    old = DeployResponse.model_validate(
        {"project_id": str(UUID(int=2)), "phase": "building", "logs": ["prepare_ms=1"]}
    )
    assert old.format_version == 1 and old.stage is None and old.progress is None
    assert old.stages == [] and old.metrics == {} and old.heartbeat_at is None
    assert json.loads(old.model_dump_json())["format_version"] == 1


async def test_start_hands_the_trace_to_the_boundary_as_observer(tmp_path, monkeypatch):
    """P13: the trusted boundary reports its own readiness steps into the
    publication trace; without a trace nothing changes."""
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services import cell_publication as module
    from tests.test_cell_publication import restored_request
    from tests.test_project_machine_manifest import payload

    service = module.CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = restored_request()
    service._effective_request = lambda request_: request_
    service._write(value.project_id, {"project_id": str(value.project_id), "history": []})
    backend = SimpleNamespace(
        project_postgres_password="private-test",
        switch_code=lambda *args: None,
        start_service=lambda *args: None,
        service_status=lambda *args: {"ready": True},
        schema_digest=lambda: "live-schema",
    )
    service._backend = lambda *args: backend
    seen: list[dict] = []

    def boundary(*_args, **kwargs):
        seen.append(kwargs)
        observer = kwargs.get("observer")
        if observer is not None:
            observer.stage("verify_core")
            observer.stage("gateway")

    manager = SimpleNamespace(machine_runtime=SimpleNamespace(_start_boundary=boundary))
    monkeypatch.setattr(module, "ensure_managed_infrastructure", AsyncMock())
    release = {
        "manifest": payload(),
        "schema_digest": "live-schema",
        "epoch": 2,
        "image_id": "image",
        "prod_url": "https://app.example.test",
    }
    trace = PublicationTrace()
    await service._start(manager, object(), release, value, switch=True, trace=trace)
    assert seen[-1]["observer"] is trace
    trace.end_stage()
    assert [item["stage"] for item in trace.snapshot()["stages"]] == [
        "start_app",
        "verify_runtime",
        "verify_core",
        "gateway",
    ]
    await service._start(manager, object(), release, value, switch=True)
    assert "observer" not in seen[-1]


def test_gateway_starts_with_a_full_core_and_settles_at_the_steady_quota():
    """P13/P17: the gateway's start-up burned ~25 s in CPU throttling at 5% of a
    core (measured on production). It is created with a full core and lowered to
    the steady quota before its identity receipt is taken."""
    import inspect

    from omnia_orchestrator.services import machine_adapter as module

    assert module._GATEWAY_BOOST_QUOTA == module._GATEWAY_CPU_PERIOD  # one core
    assert module._GATEWAY_STEADY_QUOTA * 20 == module._GATEWAY_CPU_PERIOD  # 5%
    source = inspect.getsource(module.MachineAdapter._start_boundary)
    created = source.index("cpu_quota=_GATEWAY_BOOST_QUOTA")
    ready = source.index('"/__omnia/identity", expected=401, timeout=30)', created)
    lowered = source.index("cpu_quota=_GATEWAY_STEADY_QUOTA", ready)
    receipt = source.index('trusted_container_identity(gateway, "max-gateway")', lowered)
    assert created < ready < lowered < receipt
    assert "nano_cpus=50_000_000" not in source  # NanoCpus cannot be combined with a quota
