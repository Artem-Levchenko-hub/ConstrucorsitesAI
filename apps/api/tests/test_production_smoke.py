"""Offline production smoke: independent releases, mandatory gates and safe CLI output."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from omnia_api.ops.production_smoke import Configuration, Reply, run_smoke


def environment():
    return {
        "PLATFORM_URL": "https://platform.invalid",
        "MAX_CANARY_URL": "https://canary.invalid",
        "PRODUCTION_EXPECTED_WEB_RELEASE_SHA": "a" * 40,
        "PRODUCTION_EXPECTED_API_RELEASE_SHA": "b" * 40,
        "PRODUCTION_EXPECTED_WORKER_RELEASE_SHA": "c" * 40,
        "PRODUCTION_EXPECTED_ORCHESTRATOR_RELEASE_SHA": "d" * 40,
    }


def responses():
    return {
        "/web-health": {"status": "ok", "service": "web", "release_sha": "a" * 40},
        "/api/health": {
            "status": "ok",
            "service": "api",
            "release_sha": "b" * 40,
            "checks": dict.fromkeys(
                [
                    "database",
                    "redis",
                    "worker",
                    "generation_worker",
                    "deploy_control_plane",
                    "preview_storage",
                ],
                "ok",
            ),
            "dependencies": {
                "worker_release_sha": "c" * 40,
                "generation_worker_release_sha": "c" * 40,
                "orchestrator_release_sha": "d" * 40,
            },
        },
        "/mvp": "Путь до полностью рабочего MVP",
        "canary/api/health": {"status": "ok", "platform": "max-miniapp"},
        "canary/api/max/webhook": Reply(401, b""),
    }


class HTTPDouble:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def request(self, method, url, *, timeout):
        self.calls.append((method, url, timeout))
        key = url.replace("https://platform.invalid", "").replace(
            "https://canary.invalid/", "canary/"
        )
        value = self.replies[key]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, Reply):
            return value
        return Reply(200, (value if isinstance(value, str) else json.dumps(value)).encode("utf-8"))


def test_distinct_exact_component_releases_pass_all_gates():
    http = HTTPDouble(responses())
    assert run_smoke(Configuration.from_env(environment()), http, sleep=lambda _: None) == []
    assert len(http.calls) == 5
    assert http.calls[-1][:2] == ("POST", "https://canary.invalid/api/max/webhook")
    assert all(0 < timeout <= 20 for _, _, timeout in http.calls)


@pytest.mark.parametrize(
    "component,path,field",
    [
        ("web", "/web-health", "release_sha"),
        ("api", "/api/health", "release_sha"),
        ("worker", "/api/health", "worker_release_sha"),
        ("generation_worker", "/api/health", "generation_worker_release_sha"),
        ("orchestrator", "/api/health", "orchestrator_release_sha"),
    ],
)
@pytest.mark.parametrize("actual", ["f" * 40, "unknown", None])
def test_every_component_requires_exact_nonempty_release(component, path, field, actual):
    data = responses()
    target = data[path] if field == "release_sha" else data[path]["dependencies"]
    if actual is None:
        target.pop(field)
    else:
        target[field] = actual
    failures = run_smoke(
        Configuration.from_env(environment()), HTTPDouble(data), sleep=lambda _: None
    )
    assert failures == [f"{component}.release_mismatch"]


@pytest.mark.parametrize(
    "check",
    ["database", "redis", "worker", "generation_worker", "deploy_control_plane", "preview_storage"],
)
def test_every_readiness_check_is_required(check):
    data = responses()
    del data["/api/health"]["checks"][check]
    assert run_smoke(Configuration.from_env(environment()), HTTPDouble(data)) == [
        f"api.readiness.{check}"
    ]


@pytest.mark.parametrize(
    "path,field,value,expected",
    [
        ("/web-health", "status", "degraded", "web.status"),
        ("/web-health", "service", "api", "web.service"),
        ("/api/health", "service", "web", "api.service"),
        ("/api/health", "status", "degraded", "api.status"),
        ("canary/api/health", "platform", "other", "max_health.platform"),
        ("canary/api/health", "status", "failed", "max_health.status"),
    ],
)
def test_health_contracts_are_not_reduced_to_http200(path, field, value, expected):
    data = responses()
    data[path][field] = value
    assert run_smoke(Configuration.from_env(environment()), HTTPDouble(data)) == [expected]


def test_max502_remains_red_and_webhook_is_still_checked_without_leaking_body():
    data = responses()
    data["canary/api/health"] = Reply(502, b"secret backend body")
    data["canary/api/max/webhook"] = Reply(200, b"private token")
    http = HTTPDouble(data)
    assert run_smoke(Configuration.from_env(environment()), http, sleep=lambda _: None) == [
        "max_health.http_502",
        "max_webhook.http_200",
    ]
    assert len(http.calls) == 7


def test_malformed_json_and_missing_mvp_text_fail_safely():
    data = responses()
    data["/web-health"] = Reply(200, b"not json private body")
    data["/mvp"] = "other page"
    assert run_smoke(Configuration.from_env(environment()), HTTPDouble(data)) == [
        "web.invalid_json",
        "mvp.text_missing",
    ]


def test_missing_expected_release_never_accepts_unknown():
    env = environment()
    del env["PRODUCTION_EXPECTED_WORKER_RELEASE_SHA"]
    with pytest.raises(ValueError, match=r"^config\.expected_worker_release$"):
        Configuration.from_env(env)


@pytest.mark.parametrize(
    "max_status,webhook_status,expected",
    [
        (200, 401, "PASS production_smoke"),
        (502, 401, "FAIL max_health.http_502"),
        (200, 200, "FAIL max_webhook.http_200"),
    ],
)
def test_stdlib_cli_uses_local_http_and_does_not_import_api_dependencies(
    max_status, webhook_status, expected
):
    data = responses()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            key = "canary/api/health" if self.path == "/canary/api/health" else self.path
            value = data[key]
            body = (value if isinstance(value, str) else json.dumps(value)).encode("utf-8")
            self.send_response(max_status if key == "canary/api/health" else 200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append(
                (
                    self.path,
                    self.headers.get("Content-Type"),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            self.send_response(webhook_status)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {**os.environ, **environment()}
        env["PLATFORM_URL"] = f"http://127.0.0.1:{server.server_port}"
        env["MAX_CANARY_URL"] = env["PLATFORM_URL"] + "/canary"
        env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        result = subprocess.run(
            [sys.executable, "-S", "-m", "omnia_api.ops.production_smoke"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == int(expected.startswith("FAIL")), result.stderr
        assert result.stdout.strip() == expected
        assert result.stderr == ""
        assert requests == [("/canary/api/max/webhook", "application/json", b"{}")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("name", ["PLATFORM_URL", "MAX_CANARY_URL"])
@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://user:secret@host.invalid",
        "https://host.invalid/path?token=secret",
        "https://host.invalid:bad",
        "not-a-url",
    ],
)
def test_invalid_or_missing_target_is_a_named_configuration_failure(name, value):
    env = environment()
    env[name] = value
    label = "platform_url" if name == "PLATFORM_URL" else "canary_url"
    with pytest.raises(ValueError, match=f"^config.{label}$"):
        Configuration.from_env(env)


def test_transport_retry_and_body_limits_keep_private_details_out_of_failures():
    from omnia_api.ops.production_smoke import MAX_BODY_BYTES

    data = responses()
    data["/web-health"] = OSError("https://private.invalid/?token=secret")
    data["/mvp"] = Reply(200, b"x" * (MAX_BODY_BYTES + 1))
    http = HTTPDouble(data)
    assert run_smoke(Configuration.from_env(environment()), http, sleep=lambda _: None) == [
        "web.transport_error",
        "mvp.body_too_large",
    ]
    assert len(http.calls) == 7


def test_total_deadline_stops_additional_requests():
    now = [0.0]
    http = HTTPDouble(responses())
    request = http.request

    def slow_request(*args, **kwargs):
        reply = request(*args, **kwargs)
        now[0] = 150.0
        return reply

    http.request = slow_request
    assert run_smoke(Configuration.from_env(environment()), http, clock=lambda: now[0]) == [
        "api.deadline",
        "mvp.deadline",
        "max_health.deadline",
        "max_webhook.deadline",
    ]
    assert len(http.calls) == 1


def test_stdlib_transport_deadline_bounds_even_a_blocked_body(monkeypatch):
    from omnia_api.ops import production_smoke

    entered, release = threading.Event(), threading.Event()

    class Response:
        code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, size):
            assert size == production_smoke.MAX_BODY_BYTES + 1
            entered.set()
            release.wait(2)
            return b""

    class Opener:
        def open(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(production_smoke, "build_opener", lambda *_: Opener())
    try:
        with pytest.raises(TimeoutError):
            production_smoke.HTTPClient().request("GET", "https://unused.invalid", timeout=0.05)
        assert entered.is_set()
    finally:
        release.set()


@pytest.mark.parametrize("length", [7, 39, 41])
def test_expected_release_requires_full40_identity(length):
    env = environment()
    env["PRODUCTION_EXPECTED_API_RELEASE_SHA"] = "a" * length
    with pytest.raises(ValueError, match=r"^config\.expected_api_release$"):
        Configuration.from_env(env)


def test_actual_short_sha_does_not_match_expected_full40():
    data = responses()
    data["/web-health"]["release_sha"] = "a" * 7
    assert run_smoke(Configuration.from_env(environment()), HTTPDouble(data)) == [
        "web.release_mismatch"
    ]
