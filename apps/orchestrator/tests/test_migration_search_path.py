from __future__ import annotations

from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


@pytest.mark.parametrize(
    "template",
    ("max-miniapp-nextjs", "nextjs-postgres-drizzle"),
)
def test_migration_runner_keeps_foreign_keys_in_project_schema(template: str) -> None:
    source = (_TEMPLATES / template / "scripts" / "apply-migrations.mjs").read_text(
        encoding="utf-8"
    )

    assert """.replaceAll('"public".', "")""" in source
    assert source.index('.replaceAll("--> statement-breakpoint", "")') < source.index(
        """.replaceAll('"public".', "")"""
    )
