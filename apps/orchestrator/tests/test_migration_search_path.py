from __future__ import annotations

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def test_migration_runner_keeps_foreign_keys_in_project_schema() -> None:
    source = (_TEMPLATES / "max-miniapp-nextjs" / "scripts" / "apply-migrations.mjs").read_text(
        encoding="utf-8"
    )

    assert """.replaceAll('"public".', "")""" in source
    assert source.index('.replaceAll("--> statement-breakpoint", "")') < source.index(
        """.replaceAll('"public".', "")"""
    )
