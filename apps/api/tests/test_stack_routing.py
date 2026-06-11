"""Tests for auto stack-routing (P1 — owner directive 2026-06-09, last mile).

When progressive discovery decides to BUILD and recommends a container stack for
a still-static project, ``stack_routing`` flips the template, re-scaffolds the
git, and provisions the dev container. The contract that matters: the mapping is
exact, the switch is idempotent (never double-switches a project that's already a
container stack, never touches a static recommendation), and provisioning is
fail-soft (R-10 — an orchestrator hiccup never blocks the build). Git + the
orchestrator are stubbed so these stay offline and deterministic.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from omnia_api.services import orchestrator_client, stack_routing
from omnia_api.services import repo as repo_svc

# ─── discovery_stack_to_template ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("stack", "expected"),
    [
        ("static", None),
        ("fullstack", "fullstack"),
        ("nextjs_entities", "nextjs_entities"),
        ("spa", "spa"),  # Phase 7.2 — no-backend Vite stack
        ("SPA", "spa"),  # case-insensitive
        ("NEXTJS_ENTITIES", "nextjs_entities"),  # case-insensitive
        ("  fullstack  ", "fullstack"),  # trimmed
        ("", None),
        ("garbage", None),
    ],
)
def test_stack_mapping(stack: str, expected: str | None) -> None:
    assert stack_routing.discovery_stack_to_template(stack) == expected


# ─── fakes ───────────────────────────────────────────────────────────────


class _FakeProject:
    def __init__(self, template: str) -> None:
        self.id = uuid4()
        self.slug = "shop-abc123"
        self.template = template
        self.current_snapshot_id = uuid4()


class _FakeSession:
    """Minimal async session: records add/flush/commit/refresh calls."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = False
        self.flushed = False

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        # The real AsyncSession populates a server/Python-default PK on flush;
        # mirror that so switch_to_stack can read snapshot.id afterwards.
        self.flushed = True
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj: object) -> None:
        pass


# ─── switch_to_stack ─────────────────────────────────────────────────────


async def test_switch_static_is_noop() -> None:
    """A static recommendation leaves the project untouched."""
    session = _FakeSession()
    project = _FakeProject(template="blank")
    result = await stack_routing.switch_to_stack(session, project, "static")
    assert result is None
    assert project.template == "blank"
    assert not session.committed


async def test_switch_already_container_is_idempotent() -> None:
    """A project that's already a container stack is never re-switched."""
    session = _FakeSession()
    project = _FakeProject(template="nextjs_entities")
    result = await stack_routing.switch_to_stack(session, project, "fullstack")
    assert result is None
    assert project.template == "nextjs_entities"
    assert not session.committed


async def test_switch_static_to_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    """Static → nextjs_entities flips template, re-scaffolds, returns new snap."""
    calls: dict[str, object] = {}

    def _fake_init(project_id, template_dir, template_name):  # type: ignore[no-untyped-def]
        calls["init"] = (project_id, template_name)
        return "deadbeef" * 5  # 40-char fake sha

    monkeypatch.setattr(repo_svc, "init_repo", _fake_init)

    session = _FakeSession()
    project = _FakeProject(template="blank")
    old_snap = project.current_snapshot_id

    result = await stack_routing.switch_to_stack(session, project, "nextjs_entities")

    assert project.template == "nextjs_entities"
    assert calls["init"][1] == "nextjs_entities"  # scaffolded from the right tpl
    assert session.committed
    assert result is not None
    assert result != old_snap  # a fresh starter snapshot replaced the old one
    assert project.current_snapshot_id == result


# ─── ensure_provisioned ──────────────────────────────────────────────────


async def test_provision_skipped_for_static() -> None:
    """Static templates have no container — provisioning is a no-op."""
    assert await stack_routing.ensure_provisioned(uuid4(), "slug", "blank") is False


async def test_provision_container(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def _fake_provision(*, project_id, slug, template, tier):  # type: ignore[no-untyped-def]
        seen["template"] = template
        return {"state": "running"}

    monkeypatch.setattr(orchestrator_client, "provision", _fake_provision)
    ok = await stack_routing.ensure_provisioned(uuid4(), "slug", "nextjs_entities")
    assert ok is True
    assert seen["template"] == "nextjs-entities"  # mapped to orchestrator dir name


async def test_provision_spa_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 7.2 — the spa stack provisions the vite-react-spa image."""
    seen: dict[str, object] = {}

    async def _fake_provision(*, project_id, slug, template, tier):  # type: ignore[no-untyped-def]
        seen["template"] = template
        return {"state": "running"}

    monkeypatch.setattr(orchestrator_client, "provision", _fake_provision)
    ok = await stack_routing.ensure_provisioned(uuid4(), "slug", "spa")
    assert ok is True
    assert seen["template"] == "vite-react-spa"  # mapped to orchestrator dir name


async def test_provision_failsoft(monkeypatch: pytest.MonkeyPatch) -> None:
    """An orchestrator error is swallowed — the build must not be blocked."""

    async def _boom(**_: object) -> dict[str, object]:
        raise RuntimeError("orchestrator down")

    monkeypatch.setattr(orchestrator_client, "provision", _boom)
    assert await stack_routing.ensure_provisioned(uuid4(), "slug", "fullstack") is False
