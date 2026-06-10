"""Tests for the StackSpec registry (Phase 7.1 Slice A).

These pin the *behavior-identical* contract: every value the registry produces must
equal what the provisioning pipeline computed inline before the registry existed
(image-tag formula, :3000 port, template-dir == name). If a later slice changes a
default on purpose, that change shows up here first (characterization test).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnia_orchestrator.core.stack_registry import STACKS, StackSpec, get_stack

_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[1] / "templates"
)

_SHIPPED = (
    "nextjs-entities",
    "nextjs-postgres-drizzle",
    "vite-react-spa",
    "fastapi-postgres",
    "telegram-bot-aiogram",
)


@pytest.mark.parametrize("name", _SHIPPED)
def test_registered_stack_matches_inline_formula(name: str) -> None:
    spec = get_stack(name)
    # Identical to provisioner.py's old `f"omnia-template-{req.template}:dev"`.
    assert spec.image_tag == f"omnia-template-{name}:dev"
    # Identical to docker_client.py's old hardcoded "3000/tcp".
    assert spec.container_port == 3000
    # Identical to _template_source_dir(req.template).
    assert spec.template_dir == name


@pytest.mark.parametrize("name", _SHIPPED)
def test_every_registered_stack_has_a_template_dir_on_disk(name: str) -> None:
    assert (_TEMPLATES_DIR / get_stack(name).template_dir).is_dir()


def test_all_shipped_template_dirs_are_registered() -> None:
    on_disk = {p.name for p in _TEMPLATES_DIR.iterdir() if p.is_dir()}
    assert on_disk == set(STACKS), (
        "every template dir must have a registry entry (and vice versa); "
        f"on_disk={sorted(on_disk)} registered={sorted(STACKS)}"
    )


def test_unregistered_name_is_synthesized_identically() -> None:
    # An unknown template must resolve exactly as the old inline formula did —
    # never reject (fail-fast validation is a deliberate later slice, not this seam).
    spec = get_stack("some-future-stack")
    assert spec == StackSpec(
        name="some-future-stack",
        template_dir="some-future-stack",
        image_tag="omnia-template-some-future-stack:dev",
        container_port=3000,
    )


def test_registry_lookup_returns_the_declared_singleton() -> None:
    # get_stack must return the registered instance for known names, not a fresh copy,
    # so call-sites share one source of truth.
    assert get_stack("nextjs-entities") is STACKS["nextjs-entities"]
