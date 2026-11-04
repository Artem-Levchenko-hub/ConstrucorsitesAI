"""Exercise production workflow origins through the canary's HTTP boundaries."""

import asyncio
import importlib
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from tests import production_canary_fakes as fakes
from yleum_api.core import request_origin
from yleum_api.ops.production_canary import (
    CanaryConfig,
    CanaryConfigurationError,
    CanaryFailure,
    ProductionCanary,
)


def _workflow_env(monkeypatch):
    fakes.canary_env(monkeypatch)
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / ".github/workflows/production-generation-canary.yml")
        .read_text(encoding="utf-8")
    )
    step = next(
        step for step in workflow["jobs"]["canary"]["steps"]
        if step.get("name") == "Run disposable build, preview, and edit canary"
    )
    for name in ("PRODUCTION_CANARY_BASE_URL", "PRODUCTION_CANARY_PREVIEW_HOST_SUFFIXES"):
        monkeypatch.delenv(name, raising=False)
        if name in step["env"]:
            monkeypatch.setenv(name, step["env"][name])
    monkeypatch.delenv("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", raising=False)


def _run_preview(monkeypatch, host):
    _workflow_env(monkeypatch)
    monkeypatch.setattr(
        fakes,
        "SIGNED_PREVIEW_URL",
        f"https://{host}/api/omnia/preview-session"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    production = fakes.FakeProduction()
    origins = []

    def handler(request):
        origins.append(request.url.host)
        return production.handle(request)

    canary = ProductionCanary(
        CanaryConfig.from_env(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    return canary, origins, production


@pytest.mark.parametrize("host", ["demo.dev.yleum.ru", "demo.dev2.yleum.ru"])
def test_production_workflow_completes_canary_on_either_registered_preview_host(monkeypatch, host):
    canary, origins, _ = _run_preview(monkeypatch, host)

    result = canary.run()

    assert result.cleanup_complete
    assert set(origins) == {"yleum.ru", host}


@pytest.mark.parametrize(
    "host",
    [
        "demo.unknown.yleum.ru", "demo.dev.yleum.ru.attacker.example",
        "demo.notdev.yleum.ru", "dev.yleum.ru", "demo.preview.lead-generator.ru",
    ],
)
def test_production_workflow_rejects_unlisted_preview_before_http_request(monkeypatch, host):
    canary, origins, production = _run_preview(monkeypatch, host)

    with pytest.raises(CanaryFailure, match="preview session URL is invalid"):
        canary.run()

    assert host not in origins
    assert production.paths("DELETE") == [f"/api/projects/{fakes.PROJECT_ID}"]


@pytest.mark.parametrize(
    "suffixes",
    [".dev.yleum.ru,.yleum.ru", ".com", ".dev.yleum.ru,", "", ".dev.yleum.ru,*.dev2.yleum.ru"],
)
def test_preview_allowlist_rejects_every_broad_or_invalid_entry(monkeypatch, suffixes):
    fakes.canary_env(monkeypatch)
    monkeypatch.setenv("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIXES", suffixes)

    with pytest.raises(CanaryConfigurationError):
        CanaryConfig.from_env()


def test_singular_preview_configuration_remains_supported(monkeypatch):
    fakes.canary_env(monkeypatch)
    monkeypatch.delenv("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIXES", raising=False)
    monkeypatch.setenv("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", ".preview.lead-generator.ru")

    result = ProductionCanary(
        CanaryConfig.from_env(),
        transport=fakes.FakeProduction().transport(),
        sleep=lambda _seconds: None,
    ).run()

    assert result.cleanup_complete


def _guarded_transport(monkeypatch, handler):
    """Exercise real CSRF dispatch with cookies issued only by the synthetic server."""
    monkeypatch.setattr(request_origin, "get_settings", lambda: SimpleNamespace(
        web_base_url="https://yleum.ru", cors_origins_list=[], jwt_cookie_name="omnia_session",
    ))

    def serve(request):
        events = []

        async def downstream(_scope, _receive, send):
            response = handler(request)
            if request.url.path == "/api/auth/login" and response.status_code == 200:
                response.headers["set-cookie"] = (
                    "omnia_session=synthetic-server-session; Path=/; Secure; HttpOnly"
                )
            await send({"type": "http.response.start", "status": response.status_code,
                        "headers": response.headers.raw})
            await send({"type": "http.response.body", "body": response.content})

        async def receive():
            return {"type": "http.request", "body": request.content, "more_body": False}

        async def send(event):
            events.append(event)

        scope = {
            "type": "http", "method": request.method, "path": request.url.path,
            "headers": [(key.lower(), value) for key, value in request.headers.raw],
            "scheme": request.url.scheme,
            "query_string": request.url.query,
        }
        asyncio.run(request_origin.CookieOriginMiddleware(downstream)(scope, receive, send))
        return httpx.Response(events[0]["status"], headers=events[0]["headers"],
                              content=b"".join(event.get("body", b"") for event in events[1:]))

    return httpx.MockTransport(serve)


def test_generic_canary_passes_real_origin_guard_without_changing_preview_headers(monkeypatch):
    _workflow_env(monkeypatch)
    monkeypatch.setattr(fakes, "SIGNED_PREVIEW_URL", fakes.SIGNED_PREVIEW_URL.replace(
        fakes.PREVIEW_HOST, "demo.dev.yleum.ru",
    ))
    production = fakes.FakeProduction()
    calls = []

    def handle(request):
        calls.append((request.url.host, request.method, request.url.path,
                      request.headers.get("origin"), "cookie" in request.headers))
        return production.handle(request)

    canary = ProductionCanary(CanaryConfig.from_env(),
                             transport=_guarded_transport(monkeypatch, handle),
                             sleep=lambda _seconds: None)
    result = canary.run()
    assert result.cleanup_complete
    api_writes = [call for call in calls if call[0] == "yleum.ru" and call[1] != "GET"]
    assert all(call[3] == "https://yleum.ru" for call in api_writes)
    assert any(call[4] for call in api_writes)  # The normal cookie jar kept Set-Cookie.
    assert all(call[3] is None for call in calls if call[0] != "yleum.ru")


def test_canary_does_not_overwrite_an_explicit_foreign_origin(monkeypatch):
    _workflow_env(monkeypatch)
    canary = ProductionCanary(CanaryConfig.from_env(), transport=_guarded_transport(
        monkeypatch, lambda request: httpx.Response(
            200 if request.url.path.endswith("login") else 204, json={"ok": True},
        ),
    ))
    try:
        assert canary._client.post("/api/auth/login", json={
            "email": "qa@example.test", "password": "synthetic",
        }).status_code == 200
        assert canary._client.post("/api/auth/logout", headers={
            "Origin": "https://foreign.invalid",
        }).status_code == 403
        assert canary._client.post("/api/auth/logout").status_code == 204
    finally:
        canary._client.close()


def test_catalog_initial_and_reconnected_clients_pass_origin_guard_and_cleanup(monkeypatch):
    from tests.test_production_catalog_canary import admit, config

    source = importlib.import_module("yleum_api.ops.production_catalog_canary")
    cfg = config()
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, request.headers.get("origin")))
        return httpx.Response(204 if request.url.path == "/api/auth/logout" else 200,
                              json={"ok": True})

    class BrowserAPI:
        def __init__(self):
            self.client = httpx.Client(transport=_guarded_transport(monkeypatch, handle))

        def fetch(self, url, **kwargs):
            response = self.client.request(kwargs["method"], url, headers=kwargs["headers"],
                                           content=kwargs["data"])
            return SimpleNamespace(status=response.status_code, headers=dict(response.headers),
                                   body=lambda: response.content, dispose=lambda: None)

    def context():
        api = BrowserAPI()
        return SimpleNamespace(request=api, new_page=lambda: object(), close=api.client.close)

    scenario = object.__new__(source._CatalogScenario)
    scenario.config, scenario.evidence = cfg, source.CatalogEvidence()
    scenario.scope = source.CatalogScope(cfg, "QA catalog synthetic")
    admit(scenario.scope, fakes.PROJECT_ID)
    scenario.context = context()
    scenario.contexts = [scenario.context]
    scenario.authenticated_clients, scenario.logged_in = [], False
    scenario.api = ProductionCanary(cfg, transport=source._BrowserTransport(
        scenario.context.request, scenario.scope,
    ))
    scenario.api._deadline = time.monotonic() + 30
    scenario._new_context = context
    try:
        scenario._login()
        for _index in range(2):
            suffixes = ["runtime/start"]
            if _index == 1:
                suffixes.append("app-integrations/yookassa/oauth/start")
            for suffix in suffixes:
                response = scenario.api._client.post(f"/api/projects/{fakes.PROJECT_ID}/{suffix}")
                assert response.status_code == 200
            if _index == 0:
                scenario._reconnect()
        scenario.evidence.cleanup = source.cleanup_catalog(scenario.api, None, logged_in=True)
        assert scenario.evidence.cleanup == "ok"
        scenario._logout_other_sessions()
        assert scenario.evidence.cleanup == "ok"
        assert len([call for call in seen if call[1] == "/api/auth/login"]) == 2
        assert len([call for call in seen if call[1] == "/api/auth/logout"]) == 2
        assert all(call[2] == cfg.base_url for call in seen)
    finally:
        for client in scenario.authenticated_clients:
            client.close()
        for item in scenario.contexts:
            item.close()
