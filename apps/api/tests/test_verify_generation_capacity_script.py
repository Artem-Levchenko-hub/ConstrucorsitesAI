from __future__ import annotations

import json
from uuid import UUID

from scripts import verify_generation_capacity as script


def test_execute_guard_is_inert(capsys) -> None:
    assert script.main([]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "required", "step": "execute_guard"}


def test_capacity_progress_keeps_only_bounded_public_fields() -> None:
    sanitized = script._sanitize_progress(
        {
            "status": "unknown",
            "queue_position": -10,
            "capacity_reason": "secret=value",
            "detail": "postgresql://user:password@example.test/private",
            "token": "private",
        }
    )

    assert sanitized == {
        "status": "running",
        "queue_position": 0,
        "capacity_reason": None,
    }


def test_output_ids_exclude_existing_owner_identity() -> None:
    context = script.AcceptanceContext(
        label="capacity-acceptance-test",
        owner_id=UUID("22222222-2222-2222-2222-222222222222"),
    )

    assert "owner_id" not in context.ids()


def test_portable_probe_is_parameterized_and_leaves_owned_evidence() -> None:
    probe_id = UUID("11111111-1111-1111-1111-111111111111")
    source = script._portable_probe_source(probe_id)

    assert str(probe_id) in source
    assert "process.env.DATABASE_URL" in source
    assert "VALUES ($1, $2)" in source
    assert "WHERE probe_id = $1" in source
    assert "DELETE" not in source
    assert script._PORTABLE_MARKER in source
