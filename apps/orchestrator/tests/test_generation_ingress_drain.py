import importlib.util
import io
import json
from email.message import Message
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


def _http_error(
    status: int, body: bytes = b"", content_types: tuple[str, ...] = ("application/json",)
) -> HTTPError:
    headers = Message()
    for content_type in content_types:
        headers["Content-Type"] = content_type
    return HTTPError("https://yleum.ru/", status, "test", headers, io.BytesIO(body))


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
    "content_types",
    [
        (),
        ("application/octet-stream",),
        ("text/plain",),
        ("text/html",),
        ("application/json", "application/json"),
        ("application/json, application/json",),
    ],
    ids=["missing", "old-octet-stream", "plain", "html", "duplicate", "combined-duplicate"],
)
def test_valid_drain_json_with_wrong_mime_is_not_accepted(drain, monkeypatch, content_types):
    error = _http_error(503, b'{"error":{"code":"generation_draining"}}', content_types)
    monkeypatch.setattr(
        drain.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )
    with pytest.raises(RuntimeError, match="not confirmed"):
        drain.unstubbed_probe()
    assert error.fp.closed


@pytest.mark.parametrize("content_type", ["application/json", "Application/JSON; charset=utf-8"])
def test_drain_json_accepts_single_json_mime_with_optional_charset(drain, content_type):
    error = _http_error(503, b'{"error":{"code":"generation_draining"}}', (content_type,))
    try:
        assert drain.is_drain_response(error)
    finally:
        error.close()


def test_rendered_barrier_sets_json_mime_in_location_scope_without_duplicate_header(drain):
    rendered = drain.render_vhost(drain.VHOST.read_bytes())
    location = rendered.split(b"location /api/ {", 1)[1]
    before_if, after_if = location.split(b"if ($yleum_generation_drain) {", 1)
    assert b"types { }" in before_if
    assert b"default_type application/json;" in before_if
    assert b"default_type" not in after_if
    assert b"add_header" not in rendered


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


def test_end_restores_legacy_owned_barrier_without_rewriting_its_checksum(drain):
    release = "a" * 40
    original = drain.VHOST.read_bytes()
    drain.begin(release)
    state_path = drain.STATE / "active.json"
    legacy = drain.VHOST.read_bytes().replace(
        b"        types { }\n        default_type application/json;\n", b""
    )
    drain.VHOST.write_bytes(legacy)
    state = json.loads(state_path.read_text())
    state["blocked_sha"] = drain.digest(legacy)
    state_path.write_text(json.dumps(state))
    assert drain.owned(release)["blocked_sha"] == drain.digest(legacy)

    drain.end(release)

    assert drain.VHOST.read_bytes() == original
    assert not drain.MAP.exists()
    assert not state_path.exists()
    assert (drain.STATE / (release + ".original.conf")).read_bytes() == original


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
