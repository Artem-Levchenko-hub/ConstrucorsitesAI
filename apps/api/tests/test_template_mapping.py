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

from omnia_api.schemas.project import (
    Template,
    is_fullstack,
    orchestrator_template,
)


@pytest.mark.parametrize(
    "api_value,expected_orchestrator",
    [
        ("fullstack", "nextjs-postgres-drizzle"),
        ("spa", "vite-react-spa"),
        ("tgbot", "telegram-bot-aiogram"),
        ("api", "fastapi-postgres"),
    ],
)
def test_container_backed_templates_map_to_directory(
    api_value: str, expected_orchestrator: str
) -> None:
    assert orchestrator_template(api_value) == expected_orchestrator


@pytest.mark.parametrize(
    "static_value", ["blank", "landing", "portfolio", "blog"]
)
def test_static_templates_have_no_orchestrator_directory(
    static_value: str,
) -> None:
    """Static templates render via /p/<slug>, no container — mapper must
    return None so `routers/runtime.py` can pick its fallback policy."""
    assert orchestrator_template(static_value) is None


def test_unknown_template_returns_none() -> None:
    """Future / typo'd values shouldn't accidentally provision the wrong
    template — explicit None forces caller to handle the unknown."""
    assert orchestrator_template("totally-invented-stack") is None


@pytest.mark.parametrize(
    "container_template", ["fullstack", "spa", "tgbot", "api"]
)
def test_is_fullstack_true_for_container_backed(container_template: str) -> None:
    assert is_fullstack(container_template) is True


@pytest.mark.parametrize(
    "static_template", ["blank", "landing", "portfolio", "blog"]
)
def test_is_fullstack_false_for_static(static_template: str) -> None:
    assert is_fullstack(static_template) is False


def test_every_orchestrator_template_directory_exists_on_disk() -> None:
    """Drift guard: if someone deletes a template dir on the orchestrator
    side without updating the mapper, this test catches it before the
    user sees a 404 on Start. Path is relative to repo root via the
    `apps/api` package location."""
    # apps/api/tests/test_template_mapping.py → repo_root/apps/orchestrator/templates
    repo_root = Path(__file__).resolve().parents[3]
    templates_dir = repo_root / "apps" / "orchestrator" / "templates"
    for api_value in ("fullstack", "spa", "tgbot", "api"):
        directory = orchestrator_template(api_value)
        assert directory is not None
        candidate = templates_dir / directory
        assert candidate.is_dir(), (
            f"orchestrator template `{directory}` missing for api template "
            f"`{api_value}` — expected at {candidate}"
        )


def test_template_literal_includes_new_values() -> None:
    """The Pydantic `Template` literal MUST include every new template so
    `ProjectCreate` validation doesn't reject them on the way in."""
    from typing import get_args

    values = set(get_args(Template))
    assert {"fullstack", "spa", "tgbot", "api"} <= values
    assert {"blank", "landing", "portfolio", "blog"} <= values
    assert "code" in values  # owner 2026-06-18: language-agnostic source


def test_code_template_is_not_container_backed() -> None:
    """`code` (any-language source) is file-only, like the static class — it has
    NO orchestrator image, so it must NOT be in the orchestrator map. If it ever
    starts mapping to a directory, runtime.py would try to provision a container
    for plain source files (owner 2026-06-18)."""
    assert orchestrator_template("code") is None
    assert is_fullstack("code") is False
    assert "code" in set(__import__("typing").get_args(Template))
