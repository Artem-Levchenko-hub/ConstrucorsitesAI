from uuid import uuid4

import pytest
from pydantic import ValidationError

from yleum_api.schemas.integration_runtime import RuntimeLeadRequest


def test_lead_rejects_blank_name_and_invalid_contact():
    for fields in (
        {"name": "   "},
        {"name": "Test", "email": "bad"},
        {"name": "Test", "phone": "hello"},
    ):
        with pytest.raises(ValidationError):
            RuntimeLeadRequest(**fields)


def test_amocrm_payload_maps_contact_and_project_funnel():
    from yleum_api.services.amocrm import lead_body

    body = lead_body(
        RuntimeLeadRequest(name=" Alice ", phone="+7 (900) 000-00-00", email="alice@example.com"),
        {"pipeline_id": 12, "status_id": 34},
    )
    assert body["name"] == "Alice"
    assert body["pipeline_id"] == 12
    assert body["status_id"] == 34
    assert "custom_fields_values" not in body
    contact = body["_embedded"]["contacts"][0]
    assert contact["name"] == "Alice"
    assert {f["field_code"] for f in contact["custom_fields_values"]} == {"PHONE", "EMAIL"}
    assert (
        "custom_fields_values"
        not in lead_body(RuntimeLeadRequest(name="Alice"), {})["_embedded"]["contacts"][0]
    )


def test_amocrm_options_keep_real_open_stages_only():
    from yleum_api.services.amocrm import pipeline_options

    assert pipeline_options(
        {
            "_embedded": {
                "pipelines": [
                    {
                        "id": 12,
                        "name": "Sales",
                        "_embedded": {
                            "statuses": [
                                {"id": 34, "name": "New", "type": 0},
                                {"id": 142, "name": "Won", "type": 0},
                                {"id": 143, "name": "Lost", "type": 0},
                                {"id": 99, "name": "Unsorted", "type": 1},
                            ]
                        },
                    },
                ]
            }
        }
    ) == [{"id": 12, "name": "Sales", "statuses": [{"id": 34, "name": "New"}]}]


def test_amocrm_note_preserves_comment_source_and_context():
    from yleum_api.services.amocrm import note_text

    project_id = uuid4()
    text = note_text(
        RuntimeLeadRequest(name="Alice", comment="Call after 18:00", source="Coffee catering"),
        project_id,
        42,
    )
    assert "Call after 18:00" in text
    assert "Coffee catering" in text
    assert str(project_id) in text
    assert "42" in text
