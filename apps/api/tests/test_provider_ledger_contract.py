import hashlib
import json

import pytest

from yleum_api.services.provider_ledger import StatementError, parse_statement

SOURCE = b"synthetic authorized balance-ledger export; no customer data"


def document(**overrides):
    value = {
        "schema_version": 1,
        "organization_id": "qa-org-a",
        "source_kind": "balance_ledger_export",
        "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
        "operations": [
            {
                "operation_id": "qa-op-1",
                "ref_id": "qa-request-1",
                "kind": "usage",
                "delta_kopecks": -2398,
                "currency": "RUB",
            },
        ],
    }
    value.update(overrides)
    return json.dumps(value).encode()


def parse(data):
    return parse_statement(data, expected_organization_id="qa-org-a", source=SOURCE)


def test_exact_integer_kopecks_and_zero_are_not_estimates():
    result = parse(document())
    assert result.organization_id == "qa-org-a"
    assert result.operations[0].delta_kopecks == -2398
    assert result.source_sha256 == hashlib.sha256(SOURCE).hexdigest()
    payload = json.loads(document())
    payload["operations"][0]["delta_kopecks"] = 0
    assert parse(json.dumps(payload).encode()).operations[0].delta_kopecks == 0


@pytest.mark.parametrize("amount", [True, -23.98, "-2398", None, 2**63, -(2**63)])
def test_non_integer_or_overflow_amount_is_rejected(amount):
    payload = json.loads(document())
    payload["operations"][0]["delta_kopecks"] = amount
    with pytest.raises(StatementError, match="invalid_statement"):
        parse(json.dumps(payload).encode())


def test_foreign_organization_is_rejected_before_import():
    with pytest.raises(StatementError, match="organization_mismatch"):
        parse(document(organization_id="qa-org-b"))


def test_wrong_source_hash_is_rejected():
    with pytest.raises(StatementError, match="source_hash_mismatch"):
        parse(document(source_sha256="a" * 64))


def test_missing_ref_id_is_retained_for_explicit_unmatched_report():
    payload = json.loads(document())
    payload["operations"][0]["ref_id"] = None
    assert parse(json.dumps(payload).encode()).operations[0].ref_id is None


def test_correction_keeps_own_identity_and_signed_amount():
    payload = json.loads(document())
    payload["operations"].append(
        {
            "operation_id": "qa-correction-1",
            "ref_id": "qa-request-1",
            "kind": "correction",
            "delta_kopecks": 20,
            "currency": "RUB",
        }
    )
    rows = parse(json.dumps(payload).encode()).operations
    assert [(row.operation_id, row.delta_kopecks) for row in rows] == [
        ("qa-op-1", -2398),
        ("qa-correction-1", 20),
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "USD"),
        ("kind", "payment"),
        ("operation_id", ""),
        ("ref_id", "secret\nheader"),
        ("token", "private-test-token"),
    ],
)
def test_unknown_or_unsafe_fields_are_rejected_without_echoing_payload(field, value):
    payload = json.loads(document())
    payload["operations"][0][field] = value
    with pytest.raises(StatementError) as caught:
        parse(json.dumps(payload).encode())
    assert str(caught.value) == "invalid_statement"
    assert "private-test" not in str(caught.value)


def test_duplicate_json_keys_are_rejected():
    data = document().replace(
        b'"delta_kopecks": -2398', b'"delta_kopecks": -2398, "delta_kopecks": -1'
    )
    with pytest.raises(StatementError, match="invalid_statement"):
        parse(data)
