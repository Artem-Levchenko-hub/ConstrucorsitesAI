"""AV01 + AV05: the owner's last manual test as a deterministic regression.

v1: clients (name, phone, status CHECK). 3 clients created.
v2: required email (backfilled from phone — a SYNTHETIC technical value, marked
    by the reserved ``.invalid`` domain, never a verified address) + visits with
    a relation and an amount. 4th client + 3 visits created.
Restore v1: before this package the report said "data unknown" and "custom
constraints"; now it must give counts, the precise create conflict on
clients.email, treat the identical CHECK as compatible, and list the visits
functions that v1 does not have. Everything runs on disposable PostgreSQL; no
model is called.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from omnia_orchestrator.services.code_restoration_engine import (
    blocking_explanations,
    preparation_report,
)
from omnia_orchestrator.services.restoration_catalog import (
    CATALOG_SQL,
    DRIZZLE_CATALOG_JS,
    describe_catalog,
)
from omnia_orchestrator.services.restoration_data_contract import DataContract, assess_contract
from omnia_orchestrator.services.versioning.compatibility import (
    capability_diff,
    checks_from_diagnostics,
    delete_warnings,
)
from omnia_orchestrator.services.versioning.inventory import observe_inventory
from tests._versioning_pg import pg  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "versioning" / "customer_visits"


def source(version: str) -> dict[str, str]:
    root = FIXTURES / version
    return {
        str(path.relative_to(root)): path.read_text()
        for path in root.rglob("*") if path.is_file()
    }


V1_DDL = """
CREATE TABLE clients (
  id serial PRIMARY KEY, name text NOT NULL, phone text,
  status text NOT NULL DEFAULT 'new',
  created_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT clients_status_check CHECK (status in ('new','vip'))
);
INSERT INTO clients(name, phone) VALUES ('Синтетический 1','+70000000001'),
  ('Синтетический 2','+70000000002'),('Синтетический 3','+70000000003');
"""
V2_MIGRATION = """
ALTER TABLE clients ADD COLUMN email text;
UPDATE clients SET email =
  'synthetic+' || regexp_replace(phone, '\\D', '', 'g') || '@example.invalid';
ALTER TABLE clients ALTER COLUMN email SET NOT NULL;
CREATE TABLE visits (
  id serial PRIMARY KEY,
  client_id integer NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  amount numeric(12,2) NOT NULL CHECK (amount >= 0),
  visited_at timestamp NOT NULL DEFAULT now()
);
INSERT INTO clients(name, phone, email)
  VALUES ('Синтетический 4','+70000000004','synthetic+70000000004@example.invalid');
INSERT INTO visits(client_id, amount) VALUES (1, 1500.00),(2, 990.50),(4, 3200.00);
"""
# What the v1 Drizzle schema declares (see fixtures/.../v1/src/lib/db/schema.ts).
V1_CONTRACT = {"version": 1, "tables": [{
    "name": "clients",
    "columns": [
        {"name": "id", "type": "integer", "nullable": False},
        {"name": "name", "type": "text", "nullable": False},
        {"name": "phone", "type": "text"},
        {"name": "status", "type": "text", "nullable": False},
        {"name": "created_at", "type": "timestamp without time zone", "nullable": False},
    ],
    "primary_key": ["id"],
    "check_constraints": [
        {"name": "clients_status_check", "definition": "\"clients\".\"status\" in ('new','vip')"}
    ],
}]}


@pytest.fixture
def scenario(pg):  # noqa: F811
    pg.run(V1_DDL)
    pg.run(V2_MIGRATION)
    return pg


def test_restore_v1_report_is_precise_and_keeps_counts(scenario):
    catalog = json.loads(scenario.run(CATALOG_SQL))
    contract, catalog_blockers, unsupported = describe_catalog(catalog)
    # Problem 3 (false complexity): ordinary CHECK/DEFAULT no longer block.
    assert catalog_blockers == [] and unsupported == []

    # Problem 2 (unknown presence): exact counts, independent of analysis.
    inventory = observe_inventory(scenario.run, observed_on="candidate_copy",
                                  schema_analysis="complete")
    counts = {o.object: o.row_count for o in inventory.objects}
    assert inventory.presence == "present"
    assert counts["public.clients"] == 4 and counts["public.visits"] == 3

    # Problem 1 (vague reason): the direct restore names the exact conflict.
    assessment = assess_contract(DataContract.model_validate(V1_CONTRACT), contract)
    checks = checks_from_diagnostics(assessment.diagnostics)
    by_operation = {(c.operation, c.code): c for c in checks}
    assert by_operation[("clients.read", "read_compatible")].status == "compatible"
    failure = by_operation[("clients.create", "required_field_missing_on_create")]
    assert failure.status == "incompatible" and failure.object == "public.clients.email"
    assert "clients.email" in failure.explanation
    assert by_operation[("clients.write", "check_unchanged")].status == "compatible"
    # created_at is filled by now(); it is not a conflict.
    assert ("clients.create", "required_field_default_needs_confirmation") not in by_operation
    assert assessment.blockers == ["new_required_column:clients.email"]

    # Problem 4 groundwork (lost functions): v1 has no visits routes.
    capabilities = capability_diff(source("v2"), source("v1"))
    assert {(c.method, c.path) for c in capabilities.lost} == {
        ("GET", "/api/visits"), ("POST", "/api/visits"),
    }
    assert capabilities.restored == []

    all_checks = [*checks, *delete_warnings(assessment.blocked_deletes)]
    report = preparation_report(
        blockers=blocking_explanations(all_checks),
        retained=assessment.retained_columns,
        inventory=inventory,
        checks=all_checks,
        capabilities=capabilities,
    )
    assert report["format"] == 2 and report["database_state"] == "present"
    assert report["mode"] == "adapted"
    assert len(report["blockers"]) == 1 and "clients.email" in report["blockers"][0]
    assert any("clients — 4" in line and "visits — 3" in line for line in report["retained_data"])
    assert any("GET /api/visits" in line for line in report["unavailable_features"])
    # The synthetic backfill is never presented as a verified address.
    assert "example.invalid" not in json.dumps(report, ensure_ascii=False)
    # Values stayed untouched by the analysis (read-only).
    assert scenario.run("SELECT sum(amount) FROM visits;").strip() == b"5690.50"


def test_old_writer_fails_exactly_as_reported(scenario):
    # The structural verdict matches PostgreSQL's real behaviour for v1's insert.
    with pytest.raises(RuntimeError):
        scenario.run("INSERT INTO clients(name, phone) VALUES ('v1 form', '+70000000005');")
    assert scenario.run("SELECT count(*) FROM clients;").strip() == b"4"


def _drizzle_modules() -> Path | None:
    candidate = (
        Path(__file__).parents[1] / "templates" / "nextjs-postgres-drizzle" / "node_modules"
    )
    ok = (candidate / "drizzle-orm").is_dir() and (candidate / "typescript").is_dir()
    return candidate if ok and shutil.which("node") else None


@pytest.mark.skipif(_drizzle_modules() is None, reason="template node_modules not installed")
def test_real_drizzle_v1_schema_matches_live_check(scenario, tmp_path):
    workspace = tmp_path / "workspace"
    for path, text in source("v1").items():
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (workspace / "node_modules").symlink_to(_drizzle_modules())
    outcome = subprocess.run(
        ["node", "-e", DRIZZLE_CATALOG_JS], capture_output=True, text=True, timeout=60,
        env={**os.environ, "OMNIA_WORKSPACE": str(workspace)}, check=False,
    )
    assert outcome.returncode == 0, outcome.stderr[-500:]
    from omnia_orchestrator.services.restoration_catalog import normalize_type

    tables = json.loads(outcome.stdout)
    for table in tables:
        for column in table["columns"]:
            column["type"] = normalize_type(column["type"])
    historical = DataContract(version=1, tables=tables)
    contract, _, _ = describe_catalog(json.loads(scenario.run(CATALOG_SQL)))
    assessment = assess_contract(historical, contract)
    assert assessment.blockers == ["new_required_column:clients.email"]
    assert ("check_unchanged", "public.clients.clients_status_check") in {
        (d.code, d.object) for d in assessment.diagnostics
    }


def test_apply_accepts_a_contract_prepared_before_format_2(scenario):
    from omnia_orchestrator.services.code_restoration_engine import contract_matches

    catalog = json.loads(scenario.run(CATALOG_SQL))
    contract, _, _ = describe_catalog(catalog)
    legacy = contract.model_dump(mode="json")
    for table in legacy["tables"]:
        table["checks"] = [c["definition"] for c in table.pop("check_constraints")]
        for column in table["columns"]:
            column.pop("default", None)
            column.pop("identity", None)
    assert contract_matches(contract, legacy)
    assert contract_matches(contract, contract.model_dump(mode="json"))
    legacy["tables"][0]["columns"][0]["nullable"] = True
    assert not contract_matches(contract, legacy)


def test_durable_operation_keeps_format_2_fields():
    from omnia_orchestrator.services.code_restorations import CodeRestorationService

    inventory = {"presence": "present", "coverage": "complete", "schema_analysis": "complete",
                 "observed_on": "source", "objects": []}
    report = preparation_report(
        blockers=["x"],
        inventory=__import__(
            "omnia_orchestrator.services.versioning.contracts", fromlist=["InventoryReport"]
        ).InventoryReport.model_validate(inventory),
        checks=[],
        capabilities=capability_diff(source("v2"), source("v1")),
    )
    kept = CodeRestorationService._report({"report": report})
    assert kept["format"] == 2 and kept["inventory"]["presence"] == "present"
    assert kept["capabilities"]["lost"][0] == {"method": "GET", "path": "/api/visits"}
    legacy = CodeRestorationService._report({"report": {
        k: v for k, v in report.items()
        if k not in {"format", "inventory", "checks", "capabilities"}
    }})
    assert "format" not in legacy
    broken = {**report, "inventory": {**inventory, "presence": "empty"}}
    with pytest.raises(ValueError):
        CodeRestorationService._report({"report": broken})
