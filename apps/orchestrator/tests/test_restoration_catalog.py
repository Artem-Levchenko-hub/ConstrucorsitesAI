"""Catalog evidence is observed; historical declarations cannot invent live policy."""

import pytest

from omnia_orchestrator.services.restoration_catalog import normalize_type


@pytest.mark.parametrize("self_reference", [False, True])
def test_actor_foreign_key_cycles_require_adaptation_before_sql(self_reference):
    from omnia_orchestrator.services.restoration_data_contract import (
        DataContract,
        assess_contract,
        database_policy_sql,
    )

    names = ["a"] if self_reference else ["a", "b"]
    tables = [
        {
            "name": name,
            "owner_column": "owner_id",
            "columns": [
                {"name": "id", "type": "uuid"},
                {"name": "owner_id", "type": "text"},
                {"name": "parent_id", "type": "uuid"},
            ],
            "foreign_keys": [
                {"column": "parent_id", "table": names[(index + 1) % len(names)], "target": "id"}
            ],
        }
        for index, name in enumerate(names)
    ]
    contract = DataContract(version=1, tables=tables)
    assert assess_contract(contract, contract).blockers == [
        "foreign_key_cycle:" + name for name in names
    ]
    with pytest.raises(ValueError, match="cycle"):
        database_policy_sql(
            contract, epoch=1, project_id="fixture", token_secret="fixture", password="fixture"
        )


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("int", "integer"),
        ("int4", "integer"),
        ("bool", "boolean"),
        ("timestamp", "timestamp without time zone"),
        ("timestamptz", "timestamp with time zone"),
        ("varchar(80)", "character varying(80)"),
        ("uuid", "uuid"),
        ("text", "text"),
    ],
)
def test_postgresql_aliases_are_normalized_exactly(alias, canonical):
    assert normalize_type(alias) == canonical


def test_unknown_owner_is_not_implicitly_readable():
    from omnia_orchestrator.services.restoration_catalog import infer_ownership

    with pytest.raises(ValueError, match="actor"):
        infer_ownership([{"name": "private_notes", "columns": [{"name": "note", "type": "text"}]}])


def test_schema_changes_compare_primary_unique_and_fk_update():
    from omnia_orchestrator.services.restoration_data_contract import DataContract, assess_contract

    base = {
        "version": 1,
        "tables": [
            {
                "name": "contacts",
                "owner_column": "owner_id",
                "columns": [{"name": "id", "type": "uuid"}, {"name": "owner_id", "type": "text"}],
                "primary_key": ["id"],
                "unique_keys": [],
                "foreign_keys": [],
            }
        ],
    }
    old = DataContract.model_validate(base)
    for change in (
        {"primary_key": []},
        {"unique_keys": [["owner_id"]]},
        {
            "foreign_keys": [
                {"column": "id", "table": "contacts", "target": "id", "on_update": "CASCADE"}
            ]
        },
    ):
        current = {**base, "tables": [{**base["tables"][0], **change}]}
        assert (
            "constraints_changed:contacts"
            in assess_contract(old, DataContract.model_validate(current)).blockers
        )


def catalog_payload():
    return {
        "event_triggers": False,
        "tables": [
            {
                "name": "contacts",
                "columns": [
                    {"name": "id", "type": "uuid"},
                    {"name": "owner_id", "type": "text"},
                    {"name": "profile", "type": "jsonb"},
                ],
                "triggers": [],
                "primary_key": ["id"],
                "unique_keys": [],
                "checks": [],
                "foreign_keys": [],
                "custom_indexes": False,
                "custom_constraints": False,
                "composite_foreign_keys": False,
            }
        ],
    }


@pytest.mark.parametrize("flag", ["custom_indexes", "custom_constraints", "composite_foreign_keys"])
def test_unmodeled_catalog_constraints_are_blockers(flag):
    from omnia_orchestrator.services.restoration_catalog import contract_from_catalog

    data = catalog_payload()
    data["tables"][0][flag] = True
    _, blockers = contract_from_catalog(data)
    assert flag + ":contacts" in blockers


def test_enabled_event_trigger_blocks_even_empty_database():
    from omnia_orchestrator.services.restoration_catalog import contract_from_catalog

    _, blockers = contract_from_catalog({"tables": [], "event_triggers": True})
    assert blockers == ["enabled_event_triggers"]


def test_historical_json_metadata_is_not_invented_as_live_truth():
    from omnia_orchestrator.services.restoration_catalog import contract_from_catalog
    from omnia_orchestrator.services.restoration_data_contract import DataContract

    data = catalog_payload()
    live, _ = contract_from_catalog(data)
    assert live.tables[0].columns[2].json_keys is None
    declared = live.model_dump()
    declared["tables"][0]["columns"][2].update(json_keys=["note"], meaning="profile-v1")
    trusted = DataContract.model_validate(declared)
    live, _ = contract_from_catalog(data, trusted)
    assert live.tables[0].columns[2].json_keys == ["note"]
    assert live.tables[0].columns[2].meaning == "profile-v1"


def test_json_named_user_trigger_is_not_controller_proof():
    from omnia_orchestrator.services.restoration_catalog import contract_from_catalog

    data = catalog_payload()
    data["tables"][0]["triggers"] = [{"name": "json_legitimate_looking"}]
    _, blockers = contract_from_catalog(data)
    assert blockers == ["custom_triggers:contacts"]


def test_startup_sql_is_rejected_before_candidate_execution():
    from omnia_orchestrator.services.restoration_catalog import candidate_contract

    with pytest.raises(ValueError, match="startup"):
        candidate_contract(
            object(), {"package.json": '{"scripts":{"prestart":"psql -f reset.sql"}}'}
        )
