"""Async onboarding — the first-turn question batch is planned OUT OF BAND and
delivered over the WebSocket, so POST /prompt returns inside the client's 30s
budget even though Opus (via oneprovider) answers a plan call in ~60-70s.

These are DB-free unit tests of the background runner ``_run_async_onboarding``:
the slow gateway call, the session factory and ``publish_event`` are all mocked
(same style as ``test_app_errors``), so they assert the event choreography and
fail-soft behaviour without a live Postgres or gateway.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_api.routers import messages as m
from omnia_api.services.discovery import PlannedQuestion


class _FakeSession:
    """Async-context session stub: `.get` returns the fake project/message by
    model identity, `.commit` is a no-op (mirrors test_app_errors._FakeSession)."""

    def __init__(self, project: object, msg: object) -> None:
        self._project = project
        self._msg = msg
        self.committed = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def get(self, model: object, _id: object) -> object:
        return self._project if model is m.Project else self._msg

    async def commit(self) -> None:
        self.committed = True


def _patch_common(
    monkeypatch: pytest.MonkeyPatch, project: object, msg: object
) -> list[tuple[str, dict]]:
    """Wire the fake session + capture published events. Returns the event log."""
    events: list[tuple[str, dict]] = []

    async def _fake_publish(_pid: object, etype: str, data: dict) -> None:
        events.append((etype, data))

    async def _no_type_q(_prompt: str, _language: str) -> None:
        return None

    monkeypatch.setattr(m, "publish_event", _fake_publish)
    monkeypatch.setattr(m, "_maybe_result_type_question", _no_type_q)
    monkeypatch.setattr(m, "get_engine", lambda: None)
    monkeypatch.setattr(
        m, "async_sessionmaker", lambda *_a, **_k: (lambda: _FakeSession(project, msg))
    )
    return events


@pytest.mark.asyncio
async def test_run_async_onboarding_streams_and_stashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The slow Opus plan call → a known 2-question batch.
    async def _fake_plan(_prompt: str, language: str = "ru") -> list[PlannedQuestion]:
        return [
            PlannedQuestion(message="Что за продукт?", choices=("A", "B")),
            PlannedQuestion(message="Какой тон?", choices=("Строгий", "Тёплый")),
        ]

    monkeypatch.setattr(m, "plan_discovery_questions", _fake_plan)

    project = SimpleNamespace(discovery_plan=None)
    msg = SimpleNamespace(content="", tokens_in=None, tokens_out=None)
    events = _patch_common(monkeypatch, project, msg)

    await m._run_async_onboarding(uuid4(), uuid4(), "магазин кроссовок", "ru")

    types = [t for t, _ in events]
    # Placeholder streams FIRST (bubble isn't blank for the ~minute Opus thinks),
    # then the real question replaces it, the survey opens the popup, then done.
    assert types[0] == "llm.chunk"
    assert types[-1] == "llm.done"
    assert types.index("stream.sync") < types.index("onboarding.survey")
    assert types.index("onboarding.survey") < types.index("llm.done")

    # The placeholder chunk carries the "подбираю вопросы" copy, not the question.
    assert events[0][1]["delta"] == m._ASYNC_ONBOARDING_PLACEHOLDER

    # Plan stashed on the project; the assistant message got the first question.
    assert project.discovery_plan is not None
    assert len(project.discovery_plan) == 2
    assert msg.content == "Что за продукт?"
    assert msg.tokens_out == 0

    # The survey carries both planned questions + the appended palette question,
    # and the "N из M" total counts only the planned questions.
    survey_ev = next(d for t, d in events if t == "onboarding.survey")
    assert survey_ev["question_total"] == 2
    kinds = [q.get("kind") for q in survey_ev["survey"]]
    assert kinds.count("text") == 2
    assert "palette" in kinds


@pytest.mark.asyncio
async def test_run_async_onboarding_fail_soft_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A gateway blow-up must NOT hang onboarding: it degrades to the deterministic
    # batch, still stashes a plan, opens the popup and finalizes the turn.
    async def _boom_plan(_prompt: str, language: str = "ru") -> list[PlannedQuestion]:
        raise RuntimeError("gateway down")

    monkeypatch.setattr(m, "plan_discovery_questions", _boom_plan)

    project = SimpleNamespace(discovery_plan=None)
    msg = SimpleNamespace(content="", tokens_in=None, tokens_out=None)
    events = _patch_common(monkeypatch, project, msg)

    await m._run_async_onboarding(uuid4(), uuid4(), "что-нибудь", "ru")

    types = [t for t, _ in events]
    # Never hangs: the turn is finalized despite the gateway failure.
    assert types[-1] == "llm.done"
    assert "onboarding.survey" in types
    # A real (deterministic) plan was still stashed and a question streamed.
    assert project.discovery_plan
    assert msg.content and msg.content != m._ASYNC_ONBOARDING_PLACEHOLDER
