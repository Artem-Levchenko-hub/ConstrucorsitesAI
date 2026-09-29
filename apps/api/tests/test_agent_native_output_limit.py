"""A rejected provider batch must not discard independently verifiable source."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native

from .test_agent_native_truncation import turn


@pytest.fixture
def coordinated(monkeypatch):
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class CandidateRun:
    """External provider/executor double with an independently observable tree."""

    path = "src/app/page.tsx"

    def __init__(self, monkeypatch, *, boundary="steps", check="green", gap=None):
        self.boundary = boundary
        self.check = check
        self.gap = gap
        self.tree = {self.path: "original"}
        self.actions = []
        self.provider_calls = 0
        self.events = []
        self.now = datetime.now(UTC)
        self.edit_deadline = self.now + timedelta(seconds=600)
        scenario = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return scenario.now

        monkeypatch.setattr(agent_native, "datetime", Clock)
        monkeypatch.setattr(agent_native, "_call_messages", self.provider)

    async def provider(self, *args, **kwargs):
        self.provider_calls += 1
        if self.provider_calls == 1:
            return turn(("write_file", {"path": self.path, "content": "candidate"}))
        if self.boundary == "reserve":
            self.now = self.edit_deadline + timedelta(seconds=1)
        # Even the valid first command of this truncated batch is forbidden.
        return turn(
            ("write_file", {"path": self.path, "content": "CORRUPTED"}),
            ("bash", {"cmd": "NEVER_EXECUTE"}),
            ("done", {"summary": "untrusted success"}),
            stop="max_tokens",
        )

    async def execute(self, action):
        self.actions.append(action.name)
        if action.name == "write_file":
            self.tree[action.path] = action.args["content"]
            return {"ok": True}
        assert action.name == "build"
        assert self.tree == {self.path: "candidate"}
        if self.actions.count("build") == 1:
            return {"ok": True}  # Early checkpoint cannot authorize final handoff.
        if self.check == "deadline":
            raise TimeoutError("generation deadline exceeded")
        if self.check == "cancelled":
            raise asyncio.CancelledError
        if self.check == "crash":
            raise RuntimeError("private provider detail must not leak")
        return {"ok": self.check == "green"}

    def completion_check(self, files, evidence):
        assert files == self.tree
        return self.gap

    async def emit(self, event, data):
        self.events.append((event, data))

    async def run(self):
        return await agent_native.run_native_build(
            system="MAX VERIFICATION OVERRIDE", task="Change the existing page",
            execute=self.execute, emit=self.emit, portable_cell=True,
            initial_files={self.path: "original"}, completion_check=self.completion_check,
            max_steps=2 if self.boundary == "steps" else 8, max_segments=3,
            edit_deadline=self.edit_deadline,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["steps", "reserve", "repeated"])
async def test_late_output_limit_hands_candidate_to_full_finalization(
    monkeypatch, coordinated, boundary,
):
    scenario = CandidateRun(monkeypatch, boundary=boundary)
    result = await scenario.run()

    assert scenario.actions == ["write_file", "build", "build"]
    assert scenario.provider_calls == (4 if boundary == "repeated" else 2)
    assert result.files == scenario.tree == {scenario.path: "candidate"}
    assert not result.done  # Fast check is not full/security acceptance or publication.
    assert result.needs_finalization
    assert result.proof_checkpoint.fast_check_green
    assert result.proof_checkpoint.source_complete
    assert result.evidence["build_after_write"] == 1
    assert result.stop_reason == "output_limit"
    assert result.segments == 1
    assert any(data.get("reason") == "output_limit" for _, data in scenario.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("check,gap", [
    ("red", None), ("green", "Missing required source"), ("crash", None), ("deadline", None),
])
async def test_late_output_limit_cannot_handoff_failed_or_incomplete_candidate(
    monkeypatch, coordinated, check, gap,
):
    scenario = CandidateRun(monkeypatch, check=check, gap=gap)
    result = await scenario.run()

    assert scenario.actions == ["write_file", "build", "build"]
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "output_limit"
    assert result.segments == 1
    assert scenario.provider_calls == 2
    assert "private provider detail" not in result.summary
    if check != "green":
        assert not result.evidence.get("build_after_write")


@pytest.mark.asyncio
async def test_output_limit_recovery_does_not_swallow_cancellation(monkeypatch, coordinated):
    scenario = CandidateRun(monkeypatch, check="cancelled")
    with pytest.raises(asyncio.CancelledError):
        await scenario.run()
    assert scenario.actions == ["write_file", "build", "build"]


@pytest.mark.asyncio
async def test_output_limit_without_applied_writes_does_not_recover(monkeypatch, coordinated):
    scenario = CandidateRun(monkeypatch)
    scenario.provider_calls = 1  # Start directly at the rejected response.
    result = await scenario.run()
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "output_limit"
    assert scenario.actions == []


@pytest.mark.asyncio
async def test_invalid_tool_payload_does_not_use_output_limit_recovery(monkeypatch, coordinated):
    scenario = CandidateRun(monkeypatch)
    provider = scenario.provider

    async def invalid_payload(*args, **kwargs):
        response = await provider(*args, **kwargs)
        if scenario.provider_calls > 1:
            response = turn(("write_file", {"path": scenario.path}))
        return response

    monkeypatch.setattr(agent_native, "_call_messages", invalid_payload)
    result = await scenario.run()
    assert not result.done and not result.needs_finalization
    assert scenario.actions == ["write_file", "build"]


@pytest.mark.asyncio
async def test_valid_build_after_truncation_clears_output_limit_cause(monkeypatch, coordinated):
    scenario = CandidateRun(monkeypatch, boundary="repeated")
    provider = scenario.provider

    async def corrected_provider(*args, **kwargs):
        response = await provider(*args, **kwargs)
        if scenario.provider_calls == 3:
            return turn(("build", {}))
        if scenario.provider_calls == 4:
            return turn(("done", {"summary": "verified candidate"}))
        return response

    monkeypatch.setattr(agent_native, "_call_messages", corrected_provider)
    result = await scenario.run()
    assert result.done and result.stop_reason == "done"
    assert result.files == {scenario.path: "candidate"}
    assert result.evidence["build_after_write"] == 1
    assert scenario.actions == ["write_file", "build", "build"]
    assert scenario.provider_calls == 4
