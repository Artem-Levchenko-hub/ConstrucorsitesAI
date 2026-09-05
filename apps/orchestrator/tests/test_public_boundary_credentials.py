from types import SimpleNamespace
from uuid import uuid4

from omnia_orchestrator.services.machine_adapter import MachineAdapter


def test_public_token_rotation_invalidates_sessions_without_changing_preview_secret(tmp_path):
    adapter = MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "cells")), SimpleNamespace()
    )
    source_id, public_id = uuid4(), uuid4()
    state = SimpleNamespace(workspace_id=public_id)
    preview_secret = adapter.secret(source_id)
    removed = []
    containers = {}
    backend = SimpleNamespace(stem="public-test", client=SimpleNamespace(containers=containers))
    backend._lookup = lambda _, name, kind: containers.get(name)
    first = adapter._public_auth_secret(state, backend, {"MAX_BOT_TOKEN": "test-one"})
    containers["public-test-gateway"] = SimpleNamespace(
        remove=lambda **_: removed.append("gateway")
    )
    containers["public-test-max-core"] = SimpleNamespace(
        remove=lambda **_: removed.append("core"),
        attrs={"Config": {"Env": ["AUTH_SECRET=" + first]}},
    )
    assert adapter._public_auth_secret(state, backend, {"MAX_BOT_TOKEN": "test-one"}) == first
    assert removed == []
    rotated = adapter._public_auth_secret(state, backend, {"MAX_BOT_TOKEN": "test-two"})
    assert rotated != first
    assert removed == ["gateway", "core"]
    assert adapter.secret(source_id) == preview_secret


def test_public_disconnect_also_rotates_cookie_signing_secret(tmp_path):
    adapter = MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "cells")), SimpleNamespace()
    )
    state = SimpleNamespace(workspace_id=uuid4())
    backend = SimpleNamespace(
        stem="public-test", client=SimpleNamespace(containers={}), _lookup=lambda *_: None
    )
    first = adapter._public_auth_secret(state, backend, {"MAX_BOT_TOKEN": "test-one"})
    assert adapter._public_auth_secret(state, backend, {}) != first
