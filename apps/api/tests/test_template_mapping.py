"""Tests for the api-side `template` → orchestrator-template mapping.

The mapping is the seam between the public API enum (`Template`
literal in `schemas/project.py`) and the on-disk orchestrator template
directories. A regression here either breaks provision (orchestrator
returns 404 "template not found") or silently downgrades a user's
chosen stack — both invisible failures we want a test to catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yleum_api.schemas.project import (
    Template,
    is_fullstack,
    orchestrator_template,
)


def test_the_max_app_maps_to_its_directory() -> None:
    assert orchestrator_template("max_miniapp") == "max-miniapp-nextjs"


@pytest.mark.parametrize(
    "separated_value", ["blank", "landing", "portfolio", "blog", "fullstack", "spa", "tgbot", "api"]
)
def test_separated_templates_have_no_orchestrator_directory(separated_value: str) -> None:
    """Every stack of the site builder left with its template dir: the mapper
    must answer None so nothing tries to provision an image that is not built."""
    assert orchestrator_template(separated_value) is None


def test_unknown_template_returns_none() -> None:
    """Future / typo'd values shouldn't accidentally provision the wrong
    template — explicit None forces caller to handle the unknown."""
    assert orchestrator_template("totally-invented-stack") is None


def test_is_fullstack_true_only_for_the_max_app() -> None:
    assert is_fullstack("max_miniapp") is True


@pytest.mark.parametrize(
    "separated_template",
    ["blank", "landing", "portfolio", "blog", "fullstack", "spa", "tgbot", "api"],
)
def test_is_fullstack_false_for_separated_templates(separated_template: str) -> None:
    assert is_fullstack(separated_template) is False


def test_every_orchestrator_template_directory_exists_on_disk() -> None:
    """Drift guard: if someone deletes a template dir on the orchestrator
    side without updating the mapper, this test catches it before the
    user sees a 404 on Start. Path is relative to repo root via the
    `apps/api` package location."""
    # apps/api/tests/test_template_mapping.py → repo_root/apps/orchestrator/templates
    repo_root = Path(__file__).resolve().parents[3]
    templates_dir = repo_root / "apps" / "orchestrator" / "templates"
    for api_value in ("max_miniapp",):
        directory = orchestrator_template(api_value)
        assert directory is not None
        candidate = templates_dir / directory
        assert candidate.is_dir(), (
            f"orchestrator template `{directory}` missing for api template "
            f"`{api_value}` — expected at {candidate}"
        )


def test_template_literal_includes_new_values() -> None:
    """The Pydantic `Template` literal MUST keep every template so rows of the
    separated site builder still read (`ProjectCreate` itself admits only MAX)."""
    from typing import get_args

    values = set(get_args(Template))
    assert {"fullstack", "spa", "tgbot", "api", "max_miniapp"} <= values
    assert {"blank", "landing", "portfolio", "blog"} <= values  # rows still read
    assert "code" in values  # owner 2026-06-18: language-agnostic source


def test_code_template_is_not_container_backed() -> None:
    """`code` (any-language source) is file-only, like the static class — it has
    NO orchestrator image, so it must NOT be in the orchestrator map. If it ever
    starts mapping to a directory, runtime.py would try to provision a container
    for plain source files (owner 2026-06-18)."""
    assert orchestrator_template("code") is None
    assert is_fullstack("code") is False
    assert "code" in set(__import__("typing").get_args(Template))
