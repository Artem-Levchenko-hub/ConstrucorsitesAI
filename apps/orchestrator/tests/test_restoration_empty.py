from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID


def _payload(**changes):
    value = {
        "superuser": True,
        "row_security_off": True,
        "active_sessions": 0,
        "large_objects": False,
        "prepared_transactions": False,
        "subscriptions": False,
        "replication_slots": False,
        "database_layout_valid": True,
        "extensions": ["plpgsql"],
        "relations": [
            {"schema": "public", "name": "orders", "kind": "table", "row_count": 0},
            {
                "schema": "public",
                "name": "__omnia_migrations",
                "kind": "table",
                "row_count": 4,
                "ledger_attestation": {"shape_attested": True, "rows_digest": "a" * 64},
            },
        ],
        "sequences": [],
    }
    value.update(changes)
    return value


def _template_payload(**changes):
    value = {
        "database": "template1",
        "superuser": True,
        "row_security_off": True,
        "active_sessions": 0,
        "large_objects": False,
        "prepared_transactions": False,
        "extensions": ["plpgsql"],
        "schemas": ["public"],
        "relations": 0,
        "routines": 0,
        "types": 0,
        "default_privileges": 0,
    }
    value.update(changes)
    return value


def _admin_result(main, template=None):
    template = _template_payload() if template is None else template

    def result(_backend, sql, **_kwargs):
        value = template if "\\connect template1" in sql else main
        return json.dumps(value).encode()

    return result


def test_empty_witness_is_one_repeatable_read_exact_snapshot(monkeypatch):
    from yleum_orchestrator.services import restoration_empty as module

    seen = []
    monkeypatch.setattr(
        module,
        "admin_sql",
        lambda backend, sql, **kwargs: (
            seen.append(sql) or _admin_result(_payload())(backend, sql, **kwargs)
        ),
    )

    witness = module.observe_empty_database(
        SimpleNamespace(),
        operation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        database_identity_digest="a" * 64,
        observation_kind="source",
    )

    assert witness is not None
    assert witness.all_business_empty is True
    assert witness.classification_policy == "business_data_empty_v1"
    assert len(witness.digest()) == 64
    assert len(seen) == 2
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" in seen[0]
    assert "SET LOCAL row_security = off" in seen[0]
    assert "query_to_xml" in seen[0]
    assert "pg_prepared_xacts" in seen[0]
    assert "pg_subscription" in seen[0]
    assert "\\connect template1" in seen[1]
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" in seen[1]


def test_empty_witness_fails_closed_for_nonzero_unknown_or_advanced_state(monkeypatch):
    from yleum_orchestrator.services import restoration_empty as module

    unsafe = [
        _payload(
            relations=[{"schema": "public", "name": "orders", "kind": "table", "row_count": 1}]
        ),
        _payload(
            relations=[
                {"schema": "public", "name": "remote", "kind": "foreign_table", "row_count": None}
            ]
        ),
        _payload(
            sequences=[
                {"schema": "public", "name": "orders_id_seq", "last_value": 2, "is_called": True}
            ]
        ),
        _payload(active_sessions=1),
        _payload(extensions=["plpgsql", "unsafe_extension"]),
        _payload(database_layout_valid=False),
        _payload(
            relations=[
                {
                    "schema": "public",
                    "name": "__omnia_migrations",
                    "kind": "table",
                    "row_count": 3,
                    "ledger_attestation": {"shape_attested": False, "rows_digest": "a" * 64},
                }
            ]
        ),
    ]
    payloads = iter(unsafe)
    monkeypatch.setattr(
        module,
        "admin_sql",
        lambda _backend, sql, **_kwargs: json.dumps(
            _template_payload() if "\\connect template1" in sql else next(payloads)
        ).encode(),
    )

    for index, _ in enumerate(unsafe):
        assert (
            module.observe_empty_database(
                SimpleNamespace(),
                operation_id=UUID(int=1),
                workspace_id=UUID(int=2),
                project_id=UUID(int=3),
                database_identity_digest="a" * 64,
                observation_kind="quiesced_source" if index == 3 else "source",
            )
            is None
        )


def test_quiesced_witness_matches_prepare_semantics_but_has_distinct_receipt(monkeypatch):
    from yleum_orchestrator.services import restoration_empty as module

    monkeypatch.setattr(module, "admin_sql", _admin_result(_payload()))
    kwargs = dict(
        operation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        database_identity_digest="a" * 64,
    )
    prepared = module.observe_empty_database(SimpleNamespace(), observation_kind="source", **kwargs)
    quiesced = module.observe_empty_database(
        SimpleNamespace(), observation_kind="quiesced_source", **kwargs
    )

    assert prepared is not None and quiesced is not None
    assert module.same_empty_source(prepared, quiesced)
    assert prepared.digest() != quiesced.digest()


def test_canonical_max_users_rows_are_identity_state_not_business_data(monkeypatch):
    from yleum_orchestrator.services import restoration_empty as module

    attestation = {
        "owner_is_controller": True,
        "columns": [
            ["id", "uuid", True, "gen_random_uuid()"],
            ["max_user_id", "text", True, None],
            ["first_name", "text", True, None],
            ["last_name", "text", False, None],
            ["username", "text", False, None],
            ["language_code", "text", False, None],
            ["photo_url", "text", False, None],
            ["created_at", "timestamp with time zone", True, "now()"],
            ["updated_at", "timestamp with time zone", True, "now()"],
        ],
        "primary_key": ["id"],
        "unique_keys": [["max_user_id"]],
        "rows_digest": "b" * 64,
    }
    payload = _payload(
        relations=[
            {"schema": "public", "name": "orders", "kind": "table", "row_count": 0},
            {
                "schema": "public",
                "name": "max_users",
                "kind": "table",
                "row_count": 1,
                "max_users_attestation": attestation,
            },
        ]
    )
    monkeypatch.setattr(module, "admin_sql", _admin_result(payload))

    witness = module.observe_empty_database(
        SimpleNamespace(),
        operation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        database_identity_digest="a" * 64,
        observation_kind="source",
    )
    assert witness is not None
    assert witness.identity_rows_digest != module.canonical_digest([])

    altered = json.loads(json.dumps(payload))
    altered["relations"][1]["max_users_attestation"]["columns"].append(
        ["business_plan", "text", False, None]
    )
    monkeypatch.setattr(module, "admin_sql", _admin_result(altered))
    assert (
        module.observe_empty_database(
            SimpleNamespace(),
            operation_id=UUID(int=1),
            workspace_id=UUID(int=2),
            project_id=UUID(int=3),
            database_identity_digest="a" * 64,
            observation_kind="source",
        )
        is None
    )

    changed_rows = json.loads(json.dumps(payload))
    changed_rows["relations"][1]["max_users_attestation"]["rows_digest"] = "c" * 64
    monkeypatch.setattr(module, "admin_sql", _admin_result(changed_rows))
    target = module.observe_empty_database(
        SimpleNamespace(),
        operation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        database_identity_digest="a" * 64,
        observation_kind="candidate_copy",
    )
    assert target is not None
    assert target.identity_rows_digest != witness.identity_rows_digest


def test_template1_must_be_independently_proven_stock_and_empty(monkeypatch):
    from yleum_orchestrator.services import restoration_empty as module

    kwargs = dict(
        operation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        database_identity_digest="a" * 64,
        observation_kind="source",
    )
    unsafe = [
        _template_payload(relations=1),
        _template_payload(active_sessions=1),
        _template_payload(types=1),
        _template_payload(database="other"),
    ]
    for template in unsafe:
        monkeypatch.setattr(module, "admin_sql", _admin_result(_payload(), template))
        assert module.observe_empty_database(SimpleNamespace(), **kwargs) is None


def test_missing_database_layout_attestation_fails_closed(monkeypatch):
    from yleum_orchestrator.services import restoration_empty as module

    payload = _payload()
    del payload["database_layout_valid"]
    monkeypatch.setattr(module, "admin_sql", _admin_result(payload))
    assert (
        module.observe_empty_database(
            SimpleNamespace(),
            operation_id=UUID(int=1),
            workspace_id=UUID(int=2),
            project_id=UUID(int=3),
            database_identity_digest="a" * 64,
            observation_kind="source",
        )
        is None
    )
