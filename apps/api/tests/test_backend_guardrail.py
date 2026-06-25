"""Unit tests for the backend-authoring guardrail (G003).

Locks the single safety line: writer-authored server code may NOT reach the DB
raw (which is the only way to bypass owner/membership scoping); the fixed engine
may. Pure — no container needed.
"""

from __future__ import annotations

from omnia_api.services.backend_guardrail import (
    check_backend,
    scan_backend_safety,
    summarize,
)


def test_writer_raw_db_import_is_flagged() -> None:
    files = {
        "src/app/actions/transfer.ts": 'import { db } from "@/lib/db";\nexport async function run(){}',
    }
    v = scan_backend_safety(files)
    assert len(v) == 1
    assert v[0].path == "src/app/actions/transfer.ts"


def test_writer_drizzle_import_is_flagged() -> None:
    files = {"src/app/api/custom/route.ts": 'import { eq } from "drizzle-orm";'}
    assert len(scan_backend_safety(files)) == 1


def test_writer_raw_pool_is_flagged() -> None:
    files = {"src/app/api/report/route.ts": "const pool = new Pool({ });"}
    assert len(scan_backend_safety(files)) == 1


def test_engine_file_may_use_raw_db() -> None:
    # The fixed engine IS the safe data layer — it is allowed raw access.
    files = {
        "src/lib/entities/engine.ts": 'import { db } from "@/lib/db";\nimport { eq } from "drizzle-orm";',
        "src/lib/db/index.ts": 'import { Pool } from "pg";\nconst pool = new Pool({});',
    }
    assert scan_backend_safety(files) == []


def test_writer_using_sdk_is_safe() -> None:
    # Real custom logic through the safe surface — allowed.
    files = {
        "src/app/actions/approve.ts": (
            'import { entities } from "@/lib/sdk";\n'
            'import { getCurrentUser } from "@/lib/session";\n'
            "export async function approve(id){ /* orchestration */ }"
        ),
    }
    verdict = check_backend(files)
    assert verdict.safe is True


def test_non_server_files_ignored() -> None:
    # A .json or .css can't run a query; don't flag a coincidental string.
    files = {"src/app/page.module.css": '.db{}', "entities/Order.json": '{"db":"x"}'}
    assert scan_backend_safety(files) == []


def test_summary_lists_offending_paths() -> None:
    v = scan_backend_safety(
        {"src/app/x/route.ts": 'import { db } from "@/lib/db";'}
    )
    verdict = summarize(v)
    assert verdict.safe is False
    assert "src/app/x/route.ts" in verdict.summary
