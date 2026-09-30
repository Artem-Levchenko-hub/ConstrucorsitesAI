import importlib.util
import io
from pathlib import Path
from urllib.error import HTTPError, URLError

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
    module.unstubbed_probe = module.probe
    monkeypatch.setattr(module, "probe", lambda: None)
    return module


def _http_error(status: int, body: bytes = b"") -> HTTPError:
    return HTTPError("https://yleum.ru/", status, "test", {}, io.BytesIO(body))


def test_probe_retries_401_then_accepts_only_exact_drain_response(drain, monkeypatch):
    responses = [
        _http_error(401),
        _http_error(401),
        _http_error(503, b'{"error":{"code":"generation_draining"}}'),
    ]
    errors = responses.copy()
    requests = []
    sleeps = []

    def urlopen(request, *, timeout):
        requests.append((request, timeout))
        raise responses.pop(0)

    monkeypatch.setattr(drain.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    drain.unstubbed_probe()

    assert len(requests) == 3
    assert sleeps == [1, 1]
    assert all(request.get_method() == "POST" for request, _ in requests)
    assert all(request.data == b"{}" for request, _ in requests)
    assert all(
        "/api/projects/00000000-0000-0000-0000-000000000000/prompt" in request.full_url
        for request, _ in requests
    )
    assert all(error.fp.closed for error in errors)


def test_persistent_401_exhaustion_retains_owned_barrier(drain, monkeypatch):
    attempts = []
    sleeps = []

    def urlopen(request, *, timeout):
        attempts.append(request)
        raise _http_error(401)

    monkeypatch.setattr(drain.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    monkeypatch.setattr(drain, "probe", drain.unstubbed_probe)
    with pytest.raises(RuntimeError, match="not confirmed"):
        drain.begin("a" * 40)
    assert len(attempts) == 5
    assert sleeps == [1, 1, 1, 1]
    assert (drain.STATE / "active.json").exists()
    assert drain.MARKER in drain.VHOST.read_bytes()
    assert drain.MAP.read_bytes() == drain.MAP_BODY


@pytest.mark.parametrize(
    "body",
    [
        b'{"error":{"code":"different"}}',
        b'{"error":"generation_draining"}',
        b'{"error":',
        b"<html>503</html>",
        b'{"error":{"code":"generation_draining"}}' + b" " * 8192,
    ],
    ids=["wrong-code", "wrong-shape", "malformed-json", "html", "oversized"],
)
def test_wrong_or_malformed_503_fails_immediately_and_closes_response(drain, monkeypatch, body):
    error = _http_error(503, body)
    attempts = []
    sleeps = []

    def urlopen(request, *, timeout):
        attempts.append(request)
        raise error

    monkeypatch.setattr(drain.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="not confirmed"):
        drain.unstubbed_probe()
    assert len(attempts) == 1
    assert sleeps == []
    assert error.fp.closed


@pytest.mark.parametrize(
    "response, expected_error",
    [
        ("ok", RuntimeError),
        ("network", URLError),
    ],
)
def test_successful_http_or_network_failure_cannot_confirm_barrier(
    drain, monkeypatch, response, expected_error
):
    attempts = []
    sleeps = []

    def urlopen(request, *, timeout):
        attempts.append(request)
        if response == "network":
            raise URLError("test network failure")
        return io.BytesIO(b"unexpected success")

    monkeypatch.setattr(drain.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    with pytest.raises(expected_error):
        drain.unstubbed_probe()
    assert len(attempts) == 1
    assert sleeps == []


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
