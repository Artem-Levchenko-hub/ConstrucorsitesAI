"""P09: a code-only production release keeps the running PostgreSQL container,
its volume and the guard namespace; only the product container is retired and
recreated. Development machines keep their strict machine/database epoch fence."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from tests.test_docker_machine_backend import backend
from tests.test_published_machine_backend import published_backend
from yleum_orchestrator.core.cell_resources import CellIdentityConflict


def _postgres(epoch: int, *, removed: list | None = None):
    container = SimpleNamespace(
        id=f"pg-{epoch}",
        status="running",
        labels={"omnia.fencing_epoch": str(epoch)},
        attrs={"Config": {"Labels": {"omnia.fencing_epoch": str(epoch)}}},
    )
    container.reload = lambda: None
    container.remove = lambda **_kw: (removed or []).append(container.id)
    container.start = lambda: None
    return container


def test_published_release_accepts_an_older_database_epoch_but_never_a_newer_one(tmp_path):
    runtime = published_backend(tmp_path)
    assert runtime._postgres_epoch_compatible(5, 5) is True
    assert runtime._postgres_epoch_compatible(5, 2) is True  # database predates the release
    assert runtime._postgres_epoch_compatible(2, 5) is False
    matched = []
    runtime._project_postgres_matches = lambda pg, ns, epoch: matched.append(epoch) or True
    assert runtime._project_postgres_current(_postgres(2), "guard", 5) is True
    assert matched == [2]  # compared against the container's own epoch label
    assert runtime._project_postgres_current(_postgres(7), "guard", 5) is False


def test_development_machine_keeps_the_strict_epoch_fence(tmp_path):
    runtime = backend(tmp_path)
    assert runtime._postgres_epoch_compatible(5, 5) is True
    assert runtime._postgres_epoch_compatible(5, 2) is False
    runtime._project_postgres_matches = lambda *_a: True
    assert runtime._project_postgres_current(_postgres(2), "guard", 5) is False
    assert runtime._project_postgres_current(_postgres(5), "guard", 5) is True


def test_code_switch_stops_and_retires_only_the_product_container(tmp_path):
    runtime = published_backend(tmp_path)
    events: list[str] = []
    removed: list[str] = []
    machine = SimpleNamespace(id="app", labels={"omnia.fencing_epoch": "1"})
    machine.stop = lambda timeout=None: events.append("app.stop")
    lookups = {"app": machine}
    machine.remove = lambda **_kw: (removed.append("app"), lookups.pop("app", None))
    postgres = _postgres(1, removed=removed)
    postgres.stop = lambda timeout=None: events.append("pg.stop")
    holder = {"pg": postgres}
    postgres.remove = lambda **_kw: (removed.append(postgres.id), holder.pop("pg", None))
    runtime._container = lambda: lookups.get("app")
    runtime._project_postgres = lambda: holder.get("pg")

    runtime.stop_machine()
    assert events == ["app.stop"]
    runtime.remove_machine()
    assert removed == ["app"] and "pg.stop" not in events

    # The fenced removal used by ensure()'s recreate path is app-only in production.
    lookups["app"] = machine
    runtime.remove(expected_epoch=1)
    assert removed == ["app", "app"]
    # A full (unfenced) removal still tears down the environment's database.
    runtime.remove()
    assert removed[-1] == "pg-1"


def test_switch_code_keeps_the_database_and_quiesces_the_product(tmp_path, monkeypatch):
    runtime = published_backend(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(runtime, "assert_live_volumes", lambda _m: calls.append("live"))
    monkeypatch.setattr(runtime, "quiesce_current", lambda: calls.append("quiesce"))
    monkeypatch.setattr(runtime, "remove_machine", lambda: calls.append("remove_machine"))
    monkeypatch.setattr(runtime, "remove", lambda *_a, **_k: calls.append("remove_all"))
    monkeypatch.setattr(runtime, "_metadata", lambda: {})
    monkeypatch.setattr(
        "yleum_orchestrator.services.published_machine_backend.write_controller_json",
        lambda *_a, **_k: calls.append("metadata"),
    )
    monkeypatch.setattr(runtime, "restart_infrastructure", lambda: calls.append("infra"))
    monkeypatch.setattr(runtime, "ensure", lambda _m, _e: calls.append("ensure"))
    manifest = SimpleNamespace(model_dump=lambda mode=None: {"version": 1})
    runtime.switch_code(manifest, "sha256:" + "a" * 64, 2)
    assert calls == ["live", "quiesce", "remove_machine", "metadata", "infra", "ensure"]


def test_quiesce_current_stops_the_product_not_the_database(tmp_path, monkeypatch):
    from yleum_orchestrator.services import published_machine_backend as module

    runtime = published_backend(tmp_path)
    events: list[str] = []
    current = SimpleNamespace(
        attrs={"Config": {"Labels": {"omnia.public_release_id": str(UUID(int=3))}}}
    )
    current.reload = lambda: None
    runtime._container = lambda: current
    captured = {}

    class Old:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.workspace_volume = None
            self.release_layout = None

        def _metadata(self):
            return {"release_layout": {"workspace": "ws"}, "services": {"web": {}}}

        def prepare_capture(self):
            events.append("quiesce")

        def stop_machine(self):
            events.append("stop_machine")

        def stop(self):
            events.append("stop_all")

    monkeypatch.setattr(module, "replace", lambda _self, **kwargs: Old(**kwargs))
    runtime.quiesce_current()
    assert events == ["quiesce", "stop_machine"]
    assert captured == {"release_id": UUID(int=3)}


def test_newer_database_epoch_is_still_refused(tmp_path):
    runtime = published_backend(tmp_path)
    runtime._project_postgres = lambda: _postgres(9)
    with pytest.raises(CellIdentityConflict, match="newer"):
        runtime._ensure_project_postgres("guard", 5)
