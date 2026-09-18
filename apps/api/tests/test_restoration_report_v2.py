"""AV02: structured restoration report (format 2) next to the unchanged format 1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omnia_api.schemas.restoration import RestoreReport, RuntimeRestoration

V1 = {
    "revision": 1, "mode": "adapted", "database_state": "unknown", "changes": [],
    "retained_data": [], "unavailable_features": [], "warnings": [],
    "blockers": ["Несовместимая структура данных: custom_constraints:clients"],
    "next_actions": [],
}


def v2(**overrides):
    report = {
        **V1,
        "database_state": "present",
        "blockers": [
            "Выбранная версия создаёт записи без поля clients.email, а в текущей базе "
            "оно обязательно и не заполняется автоматически."
        ],
        "format": 2,
        "inventory": {
            "presence": "present", "coverage": "complete", "schema_analysis": "complete",
            "observed_on": "candidate_copy",
            "objects": [
                {"object": "public.clients", "kind": "table", "classification": "business",
                 "presence": "present", "row_count": 4, "count_kind": "exact"},
                {"object": "public.visits", "kind": "table", "classification": "business",
                 "presence": "present", "row_count": 3, "count_kind": "exact"},
            ],
        },
        "checks": [{
            "code": "required_field_missing_on_create", "status": "incompatible",
            "severity": "blocking", "operation": "clients.create",
            "object": "public.clients.email", "evidence": "structural_rule",
            "explanation": "Выбранная версия создаёт записи без поля clients.email.",
            "resolution": "Адаптировать форму.",
        }],
        "capabilities": {"lost": [{"method": "GET", "path": "/api/visits"}], "restored": []},
    }
    report.update(overrides)
    return report


def test_format_1_reports_still_parse_and_mean_not_measured():
    report = RestoreReport.model_validate(V1)
    assert report.format == 1
    assert report.inventory is None and report.checks == [] and report.capabilities is None


def test_format_2_report_round_trips():
    report = RestoreReport.model_validate(v2())
    assert report.inventory is not None
    assert [o.row_count for o in report.inventory.objects] == [4, 3]
    assert report.checks[0].object == "public.clients.email"
    assert RestoreReport.model_validate(report.model_dump(mode="json")) == report


@pytest.mark.parametrize(
    "mutate",
    [
        # A count as a string is not evidence.
        lambda r: r["inventory"]["objects"][0].update(row_count="4"),
        lambda r: r["inventory"]["objects"][0].update(count_kind="guessed"),
        lambda r: r["checks"][0].update(status="probably_fine"),
        lambda r: r["checks"][0].update(confidence=0.9),
        lambda r: r["inventory"].update(presence="empty"),
        lambda r: r.update(format=3),
        lambda r: r["capabilities"].update(dropped=[]),
    ],
)
def test_malformed_or_inconsistent_evidence_is_rejected(mutate):
    report = v2()
    mutate(report)
    with pytest.raises(ValidationError):
        RestoreReport.model_validate(report)


def test_presence_cannot_contradict_inventory():
    with pytest.raises(ValidationError, match="database_state"):
        RestoreReport.model_validate(v2(database_state="unknown"))


def test_ready_state_still_requires_an_unblocked_report():
    from uuid import uuid4

    base = {
        "operation_id": uuid4(), "workspace_id": uuid4(), "project_id": uuid4(),
        "owner_id": uuid4(), "phase": "ready", "revision": 1, "candidate_id": uuid4(),
        "can_apply": True, "can_cancel": True, "state": "ready",
    }
    with pytest.raises(ValidationError, match="unblocked"):
        RuntimeRestoration.model_validate({**base, "report": v2()})
    ready = v2(blockers=[], mode="exact", checks=[])
    assert RuntimeRestoration.model_validate({**base, "report": ready}).report is not None


def test_oversized_report_is_trimmed_for_the_agent_not_refused():
    from omnia_api.services.restoration_adaptation import _compatibility_report

    objects = [
        {"object": f"public.table_{index:03d}_with_a_long_descriptive_name", "kind": "table",
         "classification": "business", "presence": "present", "row_count": index + 1,
         "count_kind": "exact"}
        for index in range(450)
    ]
    passed = [{
        "code": "read_compatible", "status": "compatible", "severity": "info",
        "operation": f"table_{index}.read", "object": f"public.table_{index}",
        "evidence": "structural_rule", "explanation": "Чтение совместимо. " * 10,
    } for index in range(300)]
    report = v2(checks=[*v2()["checks"], *passed])
    report["inventory"]["objects"] = objects
    trimmed = _compatibility_report(report)
    assert trimmed is not None
    assert trimmed["inventory"] is None
    assert [c["code"] for c in trimmed["checks"]] == ["required_field_missing_on_create"]
    assert trimmed["capabilities"]["lost"] == [{"method": "GET", "path": "/api/visits"}]
