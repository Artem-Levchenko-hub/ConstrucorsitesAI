import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def drain(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / "infra/release/generation-ingress-drain.py"
    spec = importlib.util.spec_from_file_location("ingress_drain_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "VHOST", tmp_path / "yleum.ru")
    monkeypatch.setattr(module, "MAP", tmp_path / "map.conf")
    monkeypatch.setattr(module, "STATE", tmp_path / "state")
    module.VHOST.write_bytes(b"server {\n location /api/ { proxy_pass http://127.0.0.1:8200; }\n}")
    monkeypatch.setattr(module, "command", lambda *args: None)
    monkeypatch.setattr(module, "probe", lambda: None)
    return module


def test_owned_barrier_roundtrip_restores_exact_vhost_and_keeps_backup(drain):
    original = drain.VHOST.read_bytes()
    drain.begin("a" * 40)
    drain.begin("a" * 40)
    assert drain.MARKER in drain.VHOST.read_bytes()
    assert drain.owned("a" * 40)["original_sha"] == drain.digest(original)
    with pytest.raises(RuntimeError, match="another release"):
        drain.end("b" * 40)
    drain.end("a" * 40)
    assert drain.VHOST.read_bytes() == original
    assert not drain.MAP.exists()
    assert (drain.STATE / ("a" * 40 + ".original.conf")).read_bytes() == original


def test_validation_failure_restores_before_any_reload(drain, monkeypatch):
    original = drain.VHOST.read_bytes()
    calls = []

    def reject(*args):
        calls.append(args)
        raise RuntimeError("nginx -t rejected")

    monkeypatch.setattr(drain, "command", reject)
    with pytest.raises(RuntimeError):
        drain.begin("a" * 40)
    assert drain.VHOST.read_bytes() == original
    assert calls == [("nginx", "-t")]
    assert not drain.MAP.exists()


def test_failed_probe_or_unrelated_vhost_edit_never_auto_clears_barrier(drain, monkeypatch):
    monkeypatch.setattr(
        drain, "probe", lambda: (_ for _ in ()).throw(RuntimeError("not confirmed"))
    )
    with pytest.raises(RuntimeError):
        drain.begin("a" * 40)
    assert (drain.STATE / "active.json").exists()
    drain.VHOST.write_bytes(drain.VHOST.read_bytes() + b"\n# operator edit")
    with pytest.raises(RuntimeError, match="changed"):
        drain.end("a" * 40)
    assert drain.VHOST.read_bytes().endswith(b"# operator edit")
    assert drain.MAP.exists()


def test_failed_restore_reload_retains_owned_barrier(drain, monkeypatch):
    drain.begin("a" * 40)

    def reject(*args):
        if args[0] == "systemctl":
            raise RuntimeError("reload failed")

    monkeypatch.setattr(drain, "command", reject)
    with pytest.raises(RuntimeError):
        drain.end("a" * 40)
    assert drain.owned("a" * 40)


def test_ambiguous_vhost_is_rejected_without_writes(drain):
    original = b"location /api/ {} location /api/ {}"
    drain.VHOST.write_bytes(original)
    with pytest.raises(RuntimeError, match="exactly one"):
        drain.begin("a" * 40)
    assert drain.VHOST.read_bytes() == original
    assert not drain.MAP.exists()
