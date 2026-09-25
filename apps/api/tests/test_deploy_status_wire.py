"""P01 wire: the API projects the controller's publication status additively —
an older controller's answer reads as format 1 (nothing invented), a format 2
answer keeps substages, heartbeat, progress, metrics and reason codes."""

from __future__ import annotations

from yleum_api.routers.runtime import _to_deploy_status


def test_old_controller_payload_reads_as_format_1():
    status = _to_deploy_status({"run_id": "r", "phase": "building", "logs": ["prepare_ms=1"]})
    assert status.format_version == 1
    assert status.stage is None and status.heartbeat_at is None and status.progress is None
    assert status.stages == [] and status.metrics == {}
    assert status.error_stage is None and status.reason_code is None
    assert status.logs == ["prepare_ms=1"]


def test_format_2_fields_pass_through_unchanged():
    payload = {
        "run_id": "r",
        "phase": "building",
        "format_version": 2,
        "stage": "capture_volumes",
        "stage_started_at": "2026-09-18T14:55:54+00:00",
        "heartbeat_at": "2026-09-18T14:56:10+00:00",
        "progress": {"bytes_done": 734003200, "bytes_total": None, "files_done": None},
        "stages": [
            {
                "stage": "preflight",
                "started_at": "2026-09-18T14:55:48+00:00",
                "finished_at": "2026-09-18T14:55:54+00:00",
                "elapsed_ms": 6000,
                "bytes_done": 0,
                "bytes_total": None,
            }
        ],
        "metrics": {"preflight_ms": 6000},
        "error_stage": None,
        "reason_code": None,
    }
    status = _to_deploy_status(payload)
    assert status.format_version == 2 and status.stage == "capture_volumes"
    assert status.progress is not None and status.progress.bytes_total is None
    assert status.progress.bytes_done == 734003200
    assert status.stages[0].stage == "preflight" and status.stages[0].elapsed_ms == 6000
    assert status.metrics == {"preflight_ms": 6000}
    wire = status.model_dump(mode="json")
    assert wire["heartbeat_at"] == "2026-09-18T14:56:10+00:00"
    assert wire["progress"] == payload["progress"]


def test_failed_run_carries_stage_and_reason():
    status = _to_deploy_status(
        {
            "run_id": "r",
            "phase": "failed",
            "error": "publication failed (CellResourceError); retained data were not restored",
            "format_version": 2,
            "error_stage": "tls",
            "reason_code": "tls_failed",
            "metrics": {"prepare_ms": 66545, "total_ms": 90000},
        }
    )
    assert (status.error_stage, status.reason_code) == ("tls", "tls_failed")
    assert status.metrics["total_ms"] == 90000 and status.stage is None


def test_no_op_outcome_keeps_its_detail_and_metric():
    status = _to_deploy_status(
        {
            "run_id": "r",
            "phase": "done",
            "detail": "already_current",
            "prod_url": "https://app.example.test",
            "format_version": 2,
            "metrics": {"total_ms": 812, "already_current": 1},
        }
    )
    assert status.detail == "already_current" and status.metrics["already_current"] == 1
