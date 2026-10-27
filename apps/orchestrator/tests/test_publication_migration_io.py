"""No migration replay or column-based inference of ambiguous commit success."""

import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_orchestrator.services.publication_additive_plan import MigrationBinding, plan_additive


def implementation():
    name = "yleum_orchestrator.services.publication_migration_io"
    assert importlib.util.find_spec(name), "publication IO implementation missing"
    return importlib.import_module(name)


def plan():
    binding = MigrationBinding(
        uuid4(),
        uuid4(),
        uuid4(),
        "a" * 40,
        "b" * 64,
        uuid4(),
        1,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
    )
    return plan_additive(
        binding, "ALTER TABLE public.orders ADD COLUMN pickup text", {"orders": ("id",)}
    )


def test_replayed_migration_must_be_only_exact_idempotent_additions():
    m = implementation()
    p = plan()
    result = m.verify_pending(
        {"drizzle/0004.sql": "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS pickup text;"},
        {},
        p,
    )
    assert list(result) == ["drizzle/0004.sql"]
    for sql in (
        "ALTER TABLE public.orders ADD COLUMN pickup text;",
        "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS pickup text; DELETE FROM orders;",
        "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS other text;",
    ):
        with pytest.raises(m.MigrationIOError):
            m.verify_pending({"drizzle/0004.sql": sql}, {}, p)


@pytest.mark.parametrize(
    "status,precommit,match,state,effect",
    [
        ("committed", True, True, "applied", False),
        ("committed", False, True, "reconciling", True),
        ("committed", True, False, "reconciling", True),
        ("aborted", False, False, "failed", False),
        ("in progress", True, True, "reconciling", True),
        (None, True, True, "reconciling", True),
    ],
)
def test_only_exact_xid_outcome_clears_exclusion(status, precommit, match, state, effect):
    m = implementation()
    result = m.recovery_patch(status, precommit=precommit, schema_matches=match, xid="42")
    assert result["state"] == state
    assert result["effect_possible"] is effect
    if not effect:
        assert result["outcome_proof"]["xid"] == "42"


def test_status_without_recorded_xid_never_clears_exclusion():
    m = implementation()
    for status in ("aborted", "committed"):
        result = m.recovery_patch(status, precommit=True, schema_matches=True)
        assert result["effect_possible"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unlock", "worker_deadline"])
async def test_post_effect_transport_or_unlock_failure_never_clears_exclusion(monkeypatch, failure):
    m = implementation()

    class Lock:
        def hold(self, _):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            if failure == "unlock":
                raise OSError("unlock failed")

    service = SimpleNamespace(_manager=lambda _: SimpleNamespace(operation_lock=Lock()))
    io = m.PublicationMigrationIO(service)

    async def source(*_):
        return (None, None, None, None)

    def effect(request, prepared, persist, *_):
        persist({"intent_xid": "42", "effect_possible": True})
        return {"state": "reconciling", "effect_possible": True}

    async def run(call, *args):
        result = call(*args)
        if failure == "worker_deadline":
            raise TimeoutError("worker already ran")
        return result

    monkeypatch.setattr(io, "_source", source)
    monkeypatch.setattr(io, "_apply", effect)
    monkeypatch.setattr(m, "machine_effect", run)
    patches = []
    result = await io.apply(SimpleNamespace(workspace_id=uuid4()), {}, patches.append)
    assert patches and patches[0]["intent_xid"] == "42"
    assert result["effect_possible"] is True
    assert result["state"] == "reconciling"
