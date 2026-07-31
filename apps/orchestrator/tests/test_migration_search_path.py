from __future__ import annotations

from pathlib import Path

import pytest

from omnia_orchestrator.services import builder

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


def test_production_build_restores_platform_migration_runner(tmp_path: Path) -> None:
    template_dir = tmp_path / "template"
    build_dir = tmp_path / "build"
    template_runner = template_dir / "scripts" / "apply-migrations.mjs"
    overlaid_runner = build_dir / "scripts" / "apply-migrations.mjs"
    template_runner.parent.mkdir(parents=True)
    overlaid_runner.parent.mkdir(parents=True)
    template_runner.write_text("current platform runner\n", encoding="utf-8")
    overlaid_runner.write_text("stale dev runner\n", encoding="utf-8")

    builder._restore_template_owned_prod_files(template_dir, build_dir)

    assert overlaid_runner.read_text(encoding="utf-8") == "current platform runner\n"
