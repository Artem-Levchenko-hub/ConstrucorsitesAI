from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from omnia_api.services import project_memory_policy as policy
from omnia_api.services.project_memory_policy import project_memory_enabled

USER = UUID("00000000-0000-4000-8000-000000000001")


@pytest.mark.parametrize(
    ("global_enabled", "canary_users", "expected"),
    [
        (False, "", False),
        (False, str(USER), True),
        (True, "", True),
        (True, str(USER), True),
        (False, "bad-entry,00000000-0000-4000-8000-000000000002", False),
    ],
)
def test_project_memory_policy(global_enabled, canary_users, expected) -> None:
    assert (
        project_memory_enabled(
            global_enabled=global_enabled,
            canary_users=canary_users,
            user_id=USER,
        )
        is expected
    )


def test_invalid_canary_entries_log_only_a_count(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class FakeLogger:
        def warning(self, event: str, **values: object) -> None:
            events.append((event, values))

    monkeypatch.setattr(policy, "log", FakeLogger(), raising=False)

    assert project_memory_enabled(
        global_enabled=False,
        canary_users=f"first-private-value,,{USER},second-private-value",
        user_id=USER,
    )
    assert events == [
        (
            "project_memory.invalid_canary_allowlist",
            {"invalid_entry_count": 2},
        )
    ]
    assert "private-value" not in repr(events)


@pytest.mark.asyncio
async def test_context_loader_uses_authenticated_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object]] = []

    async def render(session, project_id):
        calls.append((session, project_id))
        return "<project_memory>v1</project_memory>"

    monkeypatch.setattr(
        policy,
        "get_settings",
        lambda: SimpleNamespace(
            use_project_memory=False,
            project_memory_canary_users=str(USER),
        ),
        raising=False,
    )
    monkeypatch.setattr(policy, "render_project_memory_context", render, raising=False)
    session = object()
    project_id = uuid4()

    assert (
        await policy.load_project_memory_context(
            session,
            project_id=project_id,
            user_id=USER,
        )
        == "<project_memory>v1</project_memory>"
    )
    assert calls == [(session, project_id)]


@pytest.mark.asyncio
async def test_context_loader_skips_non_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def render(_session, _project_id):
        raise AssertionError("memory renderer must stay disabled")

    monkeypatch.setattr(
        policy,
        "get_settings",
        lambda: SimpleNamespace(
            use_project_memory=False,
            project_memory_canary_users="",
        ),
        raising=False,
    )
    monkeypatch.setattr(policy, "render_project_memory_context", render, raising=False)

    assert (
        await policy.load_project_memory_context(
            object(),
            project_id=uuid4(),
            user_id=USER,
        )
        == ""
    )
