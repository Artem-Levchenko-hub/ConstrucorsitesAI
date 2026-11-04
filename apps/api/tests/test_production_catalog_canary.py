"""Offline safety checks for the real production catalog browser scenario."""

import html
import importlib
import json
import time
from importlib.util import find_spec
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from yleum_api.ops.production_canary import CanaryConfig, ProductionCanary


def module():
    name = "yleum_api.ops.production_catalog_canary"
    assert find_spec(name), "The isolated catalog acceptance harness is missing"
    return importlib.import_module(name)


def config(**changes):
    values = dict(
        base_url="https://yleum.ru",
        email="synthetic-owner@example.test",
        password="synthetic-private-password",
        expected_releases={
            key: "a" * 40 for key in ("web", "api", "worker", "generation_worker", "orchestrator")
        },
        preview_host_suffix=".dev.yleum.ru",
        overall_timeout_seconds=300,
        poll_seconds=1,
        preview_host_suffixes=(".dev.yleum.ru", ".dev2.yleum.ru"),
    )
    values.update(changes)
    return CanaryConfig(**values)


def scope():
    return module().CatalogScope(config(), "QA catalog synthetic")


def admit(current, project):
    current.authorize(
        "POST",
        "https://yleum.ru/api/projects",
        {
            "name": "QA catalog synthetic",
            "max_config": {
                "summary": module()._BRIEF,
                "content": [
                    {"id": "one", "title": "QA Латте", "price": "210 ₽", "active": True},
                    {"id": "two", "title": "QA Чай", "price": "110 ₽", "active": True},
                    {"id": "three", "title": "QA Какао", "price": "190 ₽", "active": True},
                ],
            },
        },
    )
    current.admit_project(
        {
            "id": project,
            "name": "QA catalog synthetic",
            "template": "max_miniapp",
        }
    )


@pytest.mark.parametrize("base", ["https://elsewhere.invalid", "http://yleum.ru"])
def test_credentials_cannot_be_sent_to_an_unconfigured_production_origin(base):
    with pytest.raises(ValueError):
        module().validate_catalog_config(config(base_url=base))


def test_scope_forbids_other_accounts_projects_providers_and_extra_generations():
    current = scope()
    project = str(uuid4())
    with pytest.raises(ValueError):
        current.authorize("DELETE", f"https://yleum.ru/api/projects/{project}")
    admit(current, project)
    request = {"prompt": "Synthetic task", "skip_clarify": True, "idempotency_key": "synthetic-key"}
    current.authorize("POST", f"https://yleum.ru/api/projects/{project}/prompt", request)
    current.freeze_generation()
    for method, url in [
        ("POST", f"https://yleum.ru/api/projects/{project}/prompt"),
        ("DELETE", f"https://yleum.ru/api/projects/{uuid4()}"),
        ("GET", "https://api.yookassa.ru/v3/me"),
        ("POST", f"https://yleum.ru/api/projects/{project}/app-integrations/yookassa"),
    ]:
        with pytest.raises(ValueError):
            current.authorize(method, url, request)


def test_only_exact_idempotent_first_prompt_replays_are_allowed_before_freeze():
    current = scope()
    project = str(uuid4())
    admit(current, project)
    url = f"https://yleum.ru/api/projects/{project}/prompt"
    first = {"prompt": "Synthetic task", "skip_clarify": True, "idempotency_key": "synthetic-key"}
    current.authorize("POST", url, first)
    current.authorize("POST", url, dict(first))
    with pytest.raises(ValueError):
        current.authorize("POST", url, {**first, "prompt": "different task"})


def test_preview_requires_a_signed_owned_session_response_before_any_browser_navigation():
    current = scope()
    with pytest.raises(ValueError):
        current.authorize("GET", "https://synthetic.dev.yleum.ru/")
    for url in ["https://attacker.invalid/", "https://synthetic.dev.yleum.ru/"]:
        with pytest.raises(ValueError):
            current.register_preview(url)


def test_catalog_readback_rejects_changed_ids_duplicate_rows_or_hidden_item_loss():
    expected = [
        {"id": "one", "title": "Synthetic one", "price": "210 ₽", "active": True},
        {"id": "two", "title": "Synthetic two", "price": "110 ₽", "active": False},
    ]
    assert module().catalog_signature(expected) == module().catalog_signature(
        list(reversed(expected))
    )
    for actual in [expected[:1], [expected[0], expected[0]], [{**expected[0], "id": "changed"}]]:
        with pytest.raises(ValueError):
            module().assert_catalog(actual, expected)


def test_usage_witness_detects_new_calls_even_when_price_did_not_change():
    run = str(uuid4())
    baseline = {
        "run_id": run,
        "total_cost_rub": 0,
        "stages": [
            {"id": "source", "calls": 1, "tokens_in": 10, "tokens_out": 5, "retries": 0},
        ],
    }
    changed = {**baseline, "stages": [{**baseline["stages"][0], "calls": 2}]}
    assert module().usage_signature(baseline) != module().usage_signature(changed)
    for bad in [True, -1, "1"]:
        with pytest.raises(ValueError):
            module().usage_signature({**baseline, "stages": [{"calls": bad}]})


@pytest.mark.parametrize("damage", [{"price": ""}, {"price": "210 ₽"}, {"active": False}])
def test_added_item_cannot_copy_a_dropped_price_or_inactive_flag_into_expected(damage):
    source = module()
    initial = [{"id": "old", "title": "Old", "price": "1 ₽", "active": True}]
    added = {"id": "new", "title": "QA Новый напиток", "price": "250 ₽", "active": True}
    with pytest.raises(ValueError):
        source.expected_added_item(initial, [*initial, {**added, **damage}])
    assert source.expected_added_item(initial, [*initial, added]) == added


@pytest.mark.parametrize(
    "url",
    [
        "https://elsewhere.invalid/oauth/v2/authorize",
        "https://yookassa.ru/oauth/v2/authorize?response_type=code&client_id=test&state=state&redirect_uri=https%3A%2F%2Felsewhere.invalid",
    ],
)
def test_oauth_readiness_rejects_wrong_provider_or_callback(url):
    with pytest.raises(ValueError):
        module().validate_yookassa_authorization({"authorization_url": url})


def test_oauth_readiness_requires_configuration_and_validates_only_in_memory():
    source = module()
    with pytest.raises(ValueError):
        source.assert_yookassa_available(
            {"providers": [{"key": "yookassa", "oauth_available": False}]}
        )
    source.assert_yookassa_available(
        {
            "providers": [
                {
                    "key": "yookassa",
                    "oauth_supported": True,
                    "oauth_available": True,
                }
            ]
        }
    )
    from urllib.parse import urlencode

    source.validate_yookassa_authorization(
        {
            "authorization_url": "https://yookassa.ru/oauth/v2/authorize?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": "synthetic-client",
                    "state": "a" * 40,
                    "redirect_uri": "https://yleum.ru/api/integrations/oauth/yookassa/callback",
                }
            )
        }
    )
    current = scope()
    project = str(uuid4())
    admit(current, project)
    path = f"https://yleum.ru/api/projects/{project}/app-integrations/yookassa/oauth/start"
    current.authorize("POST", path)
    with pytest.raises(ValueError):
        current.authorize("POST", path)  # A single state issuance, no retries/consent.
    with pytest.raises(ValueError):
        current.authorize("POST", "https://yleum.ru/api/support/tickets")


def test_normal_support_websocket_is_read_only_and_foreign_sockets_stay_denied():
    source = module()
    project = str(uuid4())
    scenario = object.__new__(source._CatalogScenario)
    scenario.config, scenario.scope = config(), scope()
    scenario.evidence = source.CatalogEvidence()
    scenario.logged_in = True
    admit(scenario.scope, project)

    class Socket:
        def __init__(self, url):
            self.url, self.connected, self.closed, self.sent = url, False, False, []

        def connect_to_server(self):
            self.connected = True
            return self

        def on_message(self, handler):
            self.handler = handler

        def send(self, message):
            self.sent.append(message)

        def close(self, **kwargs):
            self.closed = True

    support = Socket("wss://yleum.ru/api/ws/support")
    scenario._socket(support)
    assert support.connected and not support.closed
    support.handler('{"type":"subscribe"}')
    support.handler('{"type":"ping"}')
    assert len(support.sent) == 2
    support.handler('{"type":"message","body":"must not be sent"}')
    assert support.closed and len(support.sent) == 2
    for url in (
        "wss://elsewhere.invalid/api/ws/support",
        f"wss://yleum.ru/api/ws/projects/{uuid4()}",
    ):
        socket = Socket(url)
        scenario._socket(socket)
        assert socket.closed and not socket.connected


def test_estimated_price_provenance_does_not_block_stable_source_call_baseline():
    source = module()
    scenario = object.__new__(source._CatalogScenario)
    run = str(uuid4())
    scenario.run_id = run
    scenario.scope = SimpleNamespace(project_id=str(uuid4()))
    scenario._timeout = lambda *_: 1000
    payload = {
        "run_id": run,
        "total_cost_rub": 1,
        "stages": [
            {"id": "source", "calls": 1, "tokens_in": 10, "tokens_out": 5, "retries": 0},
        ],
        "total_cost_breakdown": {"estimated": {"calls": 1}, "unknown": {"calls": 0}},
    }
    responses = iter([payload, {**payload, "total_cost_rub": 2}, payload])
    scenario._json = lambda *_: next(responses)
    assert scenario._stable_usage(SimpleNamespace(wait_for_timeout=lambda *_: None)) == (
        run,
        (("source", 1, 10, 5, 0),),
    )


def test_real_login_401_has_login_code_and_never_creates_a_project():
    source = module()
    calls = []

    def provider(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(401, json={"error": {"code": "unauthorized"}})

    scenario = object.__new__(source._CatalogScenario)
    scenario.config, scenario.evidence = config(), source.CatalogEvidence()
    scenario.phase, scenario.logged_in = "release_failed", False
    scenario.api = ProductionCanary(config(), transport=httpx.MockTransport(provider))
    scenario.api._started_at = time.monotonic()
    scenario.api._deadline = time.monotonic() + 10
    try:
        with pytest.raises(Exception):
            scenario._login()
        assert scenario.phase == "login_failed"
        assert not scenario.logged_in
        assert calls == [("POST", "/api/auth/login")]
    finally:
        assert source.cleanup_catalog(scenario.api, None, logged_in=False) == "ok"


@pytest.mark.parametrize("status", [401, 403, 422, 429, 503])
def test_login_failure_report_retains_only_the_safe_http_status(status, capsys):
    source = module()
    calls = []

    def provider(request):
        calls.append(request.url.path)
        return httpx.Response(status if request.url.path.endswith("login") else 204,
                              json={"error": "synthetic-private-password token=hidden"})

    scenario = object.__new__(source._CatalogScenario)
    scenario.config, scenario.evidence = config(), source.CatalogEvidence()
    scenario.scope = scope()
    scenario.logged_in, scenario.authenticated_clients = False, []
    scenario.context = SimpleNamespace(new_page=lambda: None, close=lambda: None)
    scenario.contexts = [scenario.context]
    scenario.api = ProductionCanary(config(), transport=httpx.MockTransport(provider))
    scenario.api._deadline = time.monotonic() + 10
    scenario.api._assert_release_health = lambda: config().expected_releases
    result = scenario.run()
    report = result.report()
    assert report["code"] == "login_failed"
    assert report["http_status"] == status
    assert report["cleanup"] == "ok"
    assert calls == ["/api/auth/login", "/api/auth/logout"]
    output = capsys.readouterr()
    published = json.dumps(report) + output.out + output.err
    assert "synthetic-private" not in published and "token=" not in published


def test_session_issued_before_browser_failure_is_still_logged_out():
    source = module()
    requests = []

    def provider(request):
        requests.append(request.url.path)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"id": str(uuid4())},
                headers={"Set-Cookie": "omnia_session=synthetic; Secure; HttpOnly"},
            )
        return httpx.Response(204)

    scenario = object.__new__(source._CatalogScenario)
    scenario.config, scenario.evidence = config(), source.CatalogEvidence()
    scenario.evidence.page_errors = 1
    scenario.logged_in = False
    scenario.api = ProductionCanary(config(), transport=httpx.MockTransport(provider))
    scenario.api._deadline = time.monotonic() + 10
    issued = scenario.api._client
    with pytest.raises(ValueError):
        scenario._login()
    assert scenario.logged_in and issued in scenario.authenticated_clients
    old = httpx.Client(base_url="https://yleum.ru", transport=httpx.MockTransport(provider))
    scenario.api._client = old  # The reconnect error restores its previous client.
    try:
        scenario._logout_other_sessions()
        assert requests == ["/api/auth/login", "/api/auth/logout"]
        assert issued.is_closed
    finally:
        old.close()


@pytest.mark.parametrize("logout_fails", [False, True])
def test_malformed_initial_login_with_issued_cookie_still_owns_logout(logout_fails, capsys):
    source = module()
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                content=b"{invalid-json",
                headers={"Set-Cookie": "omnia_session=synthetic-private-session; Secure; HttpOnly"},
            )
        assert request.url.path == "/api/auth/logout"
        assert "cookie" in request.headers
        if logout_fails:
            raise httpx.ConnectError(
                "synthetic-private-session https://private.invalid/?token=hidden"
            )
        return httpx.Response(204)

    scenario = object.__new__(source._CatalogScenario)
    scenario.config, scenario.evidence = config(), source.CatalogEvidence()
    scenario.scope = scope()
    scenario.logged_in = False
    scenario.authenticated_clients = []
    scenario.context = SimpleNamespace(new_page=lambda: None, close=lambda: None)
    scenario.contexts = [scenario.context]
    scenario.api = ProductionCanary(config(), transport=httpx.MockTransport(provider))
    scenario.api._started_at = time.monotonic()
    scenario.api._deadline = time.monotonic() + 10
    scenario.api._assert_release_health = lambda: config().expected_releases
    result = scenario.run()
    assert calls == ["/api/auth/login", "/api/auth/logout"]
    assert not scenario.logged_in  # Cleanup responsibility is not authentication authority.
    assert result.code == "login_failed"
    assert result.report()["http_status"] == 200
    assert result.cleanup == ("failed" if logout_fails else "ok")
    output = capsys.readouterr()
    published = json.dumps(result.report()) + output.out + output.err
    assert (
        "private" not in published and "token=" not in published and "invalid-json" not in published
    )


@pytest.mark.parametrize("logout_fails", [False, True])
def test_malformed_reconnect_login_cookie_is_logged_out_after_old_client_restore(logout_fails):
    source = module()
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                content=b"not-json",
                headers={
                    "Set-Cookie": "omnia_session=synthetic-reconnect; Secure; HttpOnly",
                },
            )
        assert request.url.path == "/api/auth/logout"
        if logout_fails:
            raise httpx.ConnectError("synthetic-reconnect")
        return httpx.Response(204)

    scenario = object.__new__(source._CatalogScenario)
    scenario.config, scenario.evidence = config(), source.CatalogEvidence(cleanup="ok")
    scenario.api = ProductionCanary(config(), transport=httpx.MockTransport(provider))
    scenario.api._deadline = time.monotonic() + 10
    issued = scenario.api._client
    old = httpx.Client(base_url="https://yleum.ru", transport=httpx.MockTransport(provider))
    scenario.api._client = old
    scenario.api.new_client = lambda **_kwargs: issued
    scenario.contexts = []
    scenario._new_context = lambda: SimpleNamespace(request=None)
    scenario.scope = scope()
    scenario.authenticated_clients = [old]
    scenario.logged_in = True
    try:
        with pytest.raises(Exception):
            scenario._reconnect()
        assert scenario.api._client is old
        scenario._logout_other_sessions()
        assert calls == ["/api/auth/login", "/api/auth/logout"]
        assert issued.is_closed
        assert scenario.evidence.cleanup == ("failed" if logout_fails else "ok")
    finally:
        issued.close()
        old.close()


@pytest.mark.parametrize("denied", [False, True])
def test_oauth_readiness_uses_only_owned_public_endpoints_and_safe_diagnostics(denied):
    from urllib.parse import urlencode

    source = module()
    scenario = object.__new__(source._CatalogScenario)
    scenario.config, scenario.scope = config(), scope()
    project = str(uuid4())
    admit(scenario.scope, project)
    scenario.evidence = source.CatalogEvidence()
    calls = []

    def provider(request):
        scenario.scope.authorize(request.method, str(request.url))
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "providers": [
                        {
                            "key": "yookassa",
                            "oauth_supported": True,
                            "oauth_available": True,
                        }
                    ]
                },
            )
        if denied:
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "integration_oauth_unavailable",
                        "message": "private synthetic token",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "authorization_url": "https://yookassa.ru/oauth/v2/authorize?"
                + urlencode(
                    {
                        "response_type": "code",
                        "client_id": "synthetic-private-client",
                        "state": "z" * 40,
                        "redirect_uri": "https://yleum.ru/api/integrations/oauth/yookassa/callback",
                    }
                )
            },
        )

    scenario.api = ProductionCanary(config(), transport=httpx.MockTransport(provider))
    scenario.api._deadline = time.monotonic() + 10
    try:
        if denied:
            with pytest.raises(ValueError):
                scenario._oauth_readiness()
            assert scenario.evidence.oauth_error_code == "integration_oauth_unavailable"
        else:
            scenario._oauth_readiness()
            assert "oauth_readiness" in scenario.evidence.checks
        assert calls == [
            ("GET", f"/api/projects/{project}/app-integrations"),
            ("POST", f"/api/projects/{project}/app-integrations/yookassa/oauth/start"),
        ]
        report = json.dumps(scenario.evidence.report())
        assert (
            "private" not in report and "z" * 40 not in report and "authorization_url" not in report
        )
    finally:
        scenario.api._client.close()


@pytest.mark.parametrize("damage", ["unfinished", "run", "snapshot", "missing"])
def test_completed_status_cannot_replace_current_request_acceptance(damage):
    source = module()
    run, snapshot = str(uuid4()), str(uuid4())
    acceptance = {"run_id": run, "snapshot_id": snapshot, "current_request_complete": True}
    if damage == "unfinished":
        acceptance["current_request_complete"] = False
    elif damage == "run":
        acceptance["run_id"] = str(uuid4())
    elif damage == "snapshot":
        acceptance["snapshot_id"] = str(uuid4())
    with pytest.raises(ValueError):
        source.assert_initial_acceptance(
            {
                "id": run,
                "status": "completed",
                "response_mode": "build",
                "acceptance": None if damage == "missing" else acceptance,
            },
            run,
            snapshot,
        )


def test_unknown_creation_cleanup_never_lists_or_deletes_an_existing_project():
    requests = []

    def provider(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(204)

    canary = ProductionCanary(config(), transport=httpx.MockTransport(provider))
    assert module().cleanup_catalog(canary, None, logged_in=True) == "ok"
    assert requests == [("POST", "/api/auth/logout")]


def test_cleanup_observes_accepted_deletion_without_replaying_delete():
    project = str(uuid4())
    requests = []
    statuses = iter([202, 200, 404, 204])

    def provider(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(next(statuses))

    canary = ProductionCanary(
        config(), transport=httpx.MockTransport(provider), sleep=lambda _: None
    )
    canary._created_project_id = project
    canary._cancel_active_generation = lambda _: None
    assert module().cleanup_catalog(canary, project, logged_in=True) == "ok"
    assert [method for method, path in requests if path.endswith(project)] == [
        "DELETE",
        "GET",
        "GET",
    ]


def test_cleanup_failure_is_reported_without_original_exception_or_token(tmp_path):
    def unsafe(*args, **kwargs):
        raise RuntimeError("synthetic-private-password https://private.invalid/?token=secret")

    canary = SimpleNamespace(_cleanup=unsafe)
    assert module().cleanup_catalog(canary, str(uuid4()), logged_in=True) == "failed"
    evidence = module().CatalogEvidence()
    evidence.fail("browser_failed")
    evidence.write(tmp_path)
    text = (tmp_path / "evidence.json").read_text(encoding="utf-8")
    assert json.loads(text)["code"] == "browser_failed"
    assert "private" not in text and "token" not in text and "password" not in text


def test_cli_never_publishes_exception_text_or_credentials(monkeypatch, tmp_path, capsys):
    cli = importlib.import_module("scripts.production_catalog_canary")
    monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(tmp_path / "result.json"))
    monkeypatch.setenv("PRODUCTION_CATALOG_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(cli.CanaryConfig, "from_env", lambda: config())

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-private-password https://private.invalid/?token=secret")

    monkeypatch.setattr(cli, "run_catalog_canary", fail)
    assert cli.main() == 1
    outputs = capsys.readouterr()
    text = outputs.out + outputs.err + (tmp_path / "result.json").read_text(encoding="utf-8")
    assert "private" not in text and "token=" not in text and "password" not in text


def test_cleanup_deadline_is_bounded_after_accepted_delete():
    project = str(uuid4())
    requests = []

    def provider(request):
        requests.append(request.method)
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(202 if request.method == "DELETE" else 200)

    canary = ProductionCanary(
        config(),
        transport=httpx.MockTransport(provider),
        sleep=lambda _: None,
    )
    canary._created_project_id = project
    canary._cancel_active_generation = lambda _: None
    assert module().cleanup_catalog(canary, project, logged_in=True) == "failed"
    assert requests.count("DELETE") == 1
    assert requests.count("GET") == 11
    assert requests[-1] == "POST"  # Logout still happens when deletion cannot be proved.


def test_cli_reports_artifact_io_failure_without_a_traceback(monkeypatch, tmp_path, capsys):
    cli = importlib.import_module("scripts.production_catalog_canary")
    monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(tmp_path))
    monkeypatch.setenv("PRODUCTION_CATALOG_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(cli.CanaryConfig, "from_env", lambda: config())
    monkeypatch.setattr(cli, "run_catalog_canary", lambda *_: module().CatalogEvidence())
    assert cli.main() == 1
    output = capsys.readouterr()
    assert "Traceback" not in output.err and "Traceback" not in output.out


def test_owned_create_response_is_admitted_before_browser_receives_it():
    source = module()
    current = scope()
    project, snapshot = str(uuid4()), str(uuid4())
    payload = {
        "name": current.name,
        "max_config": {
            "summary": source._BRIEF,
            "content": [
                {"id": "one", "title": "QA Латте", "price": "210 ₽", "active": True},
                {"id": "two", "title": "QA Чай", "price": "110 ₽", "active": True},
                {"id": "three", "title": "QA Какао", "price": "190 ₽", "active": True},
            ],
        },
    }
    response_payload = {
        "id": project,
        "name": current.name,
        "template": "max_miniapp",
        "current_snapshot_id": snapshot,
    }
    response = SimpleNamespace(
        status=201,
        json=lambda: response_payload,
        body=lambda: json.dumps(response_payload).encode(),
        dispose=lambda: None,
    )
    calls = []

    def fulfill(**kwargs):
        assert current.project_id == project  # The starter may POST immediately at this point.
        calls.append("fulfill")

    def fetch(**kwargs):
        calls.append("fetch")
        return response

    route = SimpleNamespace(
        request=SimpleNamespace(
            method="POST", url="https://yleum.ru/api/projects", post_data_json=payload, headers={}
        ),
        fetch=fetch,
        fulfill=fulfill,
        abort=lambda *_: calls.append("abort"),
    )
    scenario = object.__new__(source._CatalogScenario)
    scenario.scope, scenario.config = current, config()
    scenario.evidence = source.CatalogEvidence()
    scenario.api = SimpleNamespace(_deadline=time.monotonic() + 30)
    scenario._route(route)
    assert calls == ["fetch", "fulfill"]
    assert scenario.seed_snapshot == snapshot and current.create_requests == 1


def test_next_server_action_read_is_not_misparsed_as_json():
    source = module()
    current = scope()
    admit(current, str(uuid4()))
    origin = "https://synthetic.dev.yleum.ru"
    current.register_preview(
        origin + "/api/omnia/preview-session?expires=9999999999&signature=" + "a" * 43
    )

    class ServerActionRequest:
        method, url, headers = "POST", origin + "/", {"next-action": "synthetic-read-action"}

        @property
        def post_data_json(self):
            raise AssertionError("Next server action body is not JSON")

    actions = []
    scenario = object.__new__(source._CatalogScenario)
    scenario.scope, scenario.config = current, config()
    scenario.evidence = source.CatalogEvidence()
    scenario._route(
        SimpleNamespace(
            request=ServerActionRequest(),
            continue_=lambda: actions.append("continue"),
            abort=lambda *_: actions.append("abort"),
        )
    )
    assert actions == ["continue"] and scenario.evidence.code == "ok"


def test_initial_product_dom_must_pass_before_any_catalog_mutation(tmp_path):
    source = module()
    scenario = object.__new__(source._CatalogScenario)
    project, run, snapshot = (str(uuid4()) for _ in range(3))
    scenario.evidence = source.CatalogEvidence()
    scenario.phase = "browser_failed"
    scenario.run_id = run
    scenario.seed_snapshot = snapshot
    scenario.logged_in = True
    scenario.context = SimpleNamespace(new_page=lambda: None, close=lambda: None)
    scenario.contexts = [scenario.context]
    current = [{"id": "one", "title": "QA Латте", "price": "210 ₽", "active": True}]
    scenario.scope = SimpleNamespace(
        project_id=project,
        initial_content=current,
        freeze_generation=lambda: None,
        create_requests=1,
    )
    scenario.api = SimpleNamespace(
        _assert_release_health=lambda: config().expected_releases,
        _poll_generation=lambda *_: {
            "id": run,
            "status": "completed",
            "response_mode": "build",
            "acceptance": {
                "run_id": run,
                "snapshot_id": snapshot,
                "current_request_complete": True,
            },
        },
        _assert_new_snapshot=lambda *_: snapshot,
        _start_runtime=lambda *_: None,
        _cleanup=lambda *_, **__: None,
        _started_at=time.monotonic(),
    )
    actions = []
    scenario._login = lambda: None
    scenario._wizard = lambda _: None
    scenario._catalog = lambda: {"content": current}
    scenario._stable_usage = lambda _: ()

    def mutate(*_):
        actions.append("mutation")
        return current

    def preview(*_, **__):
        actions.append("preview")
        raise ValueError("The initial product did not actually render")

    scenario._edit_catalog = mutate
    scenario._preview = preview
    result = scenario.run()
    assert actions == ["preview"]
    assert result.cleanup == "ok" and result.report()["status"] == "failure"


@pytest.fixture
def offline_browser():
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error:
            pytest.skip("Locked Playwright Chromium is not installed")
        try:
            yield browser
        finally:
            browser.close()


def gated_studio_document(fields="", *, learning="welcome"):
    """Closing is not completion: Create reopens the real first-visit UI gate."""
    return """<meta charset="utf-8"><div id="workspace" data-max-studio>
    <button id="open">Создать приложение</button>
    <div role="dialog" id="wizard" hidden><input id="max-project-name">
    <textarea id="max-project-idea"></textarea><button>Каталог</button>
    <button onclick="window.steps=(window.steps||0)+1">Далее</button>FIELDS
    <button id="create">Создать проект</button></div></div>
    <div role="dialog" id="learning" hidden></div><script>
    const mode=MODE, workspace=document.querySelector('#workspace');
    const learning=document.querySelector('#learning'), wizard=document.querySelector('#wizard');
    const exempt=mode.startsWith('exempt'); let ready=mode!=='exempt_loading';
    workspace.dataset.learningReady=String(ready);
    if(!ready)setTimeout(()=>{ready=true; workspace.dataset.learningReady='true';},1200);
    window.learningCompleted=exempt; window.learningTrace=[];
    let step=mode==='resumed'?1:0, started=mode==='resumed';
    function renderLearning(){
      learning.hidden=false; workspace.inert=true;
      learning.innerHTML='<button aria-label="Закрыть обучение" id="close">'
        +'Закрыть обучение</button>';
      document.querySelector('#close').onclick=()=>{
        window.learningTrace.push('close'); learning.hidden=true; workspace.inert=false;
      };
      const action=document.createElement('button'); learning.append(action);
      if(!started){
        action.textContent='Начать обучение'; action.onclick=()=>{
          window.learningTrace.push('start'); started=true; renderLearning();
        };
      }else if(step<3 || mode==='endless'){
        action.textContent='Далее'; action.onclick=()=>{
          window.learningTrace.push('next'); step++; renderLearning();
        };
      }else{
        action.textContent='Завершить'; action.onclick=()=>{
          window.learningTrace.push('finish'); window.learningCompleted=true;
          learning.hidden=true; workspace.inert=false;
        };
      }
    }
    document.querySelector('#open').onclick=()=>{
      if(!ready)return;
      if(!window.learningCompleted)renderLearning(); else wizard.hidden=false;
    };
    if(!exempt && mode!=='gate')renderLearning();
    </script>""".replace("FIELDS", fields).replace("MODE", json.dumps(learning))


@pytest.mark.parametrize("learning", ["welcome", "resumed", "exempt", "gate", "exempt_loading"])
@pytest.mark.parametrize("wrong_price", [False, True])
def test_real_browser_wizard_reviews_extracted_dom_before_creating(
    offline_browser,
    wrong_price,
    learning,
):
    """A wrong reviewed price must stop BEFORE a project or paid prompt is sent."""
    source = module()
    scenario = object.__new__(source._CatalogScenario)
    scenario.config = config()
    scenario.scope = source.CatalogScope(config(), "QA catalog synthetic")
    scenario.evidence = source.CatalogEvidence()
    scenario.api = SimpleNamespace(_deadline=time.monotonic() + 30)
    scenario.run_id = scenario.seed_snapshot = None
    project, snapshot, run = (str(uuid4()) for _ in range(3))
    rows = [
        {"id": "one", "title": "QA Латте", "price": "210 ₽", "active": True},
        {"id": "two", "title": "QA Чай", "price": "110 ₽", "active": True},
        {"id": "three", "title": "QA Какао", "price": "190 ₽", "active": True},
    ]
    display = [{**row} for row in rows]
    if wrong_price:
        display[0]["price"] = "999 ₽"
    fields = "".join(
        f'<label>Название позиции {index}<input value="{row["title"]}"></label>'
        f'<label>Цена позиции {index}<input value="{row["price"]}"></label>'
        for index, row in enumerate(display, 1)
    )
    document = gated_studio_document(fields, learning=learning)
    document += """<script>
    document.querySelector('#create').onclick=async()=>{
      const headers={'Content-Type':'application/json'};
      const response=await fetch('/api/projects',{method:'POST',headers,
        body:JSON.stringify({name:document.querySelector('#max-project-name').value,
          max_config:{summary:document.querySelector('#max-project-idea').value,content:ROWS}})});
      const project=await response.json();
      await fetch('/api/projects/'+project.id+'/prompt',{method:'POST',headers,
        body:JSON.stringify({prompt:'Synthetic task',skip_clarify:true,
          idempotency_key:'synthetic-key'})});
    };</script>""".replace("ROWS", json.dumps(rows))
    requests = []
    context = offline_browser.new_context(base_url="https://yleum.ru")

    def provider(route):
        request = route.request
        if request.method == "GET" and request.url == "https://yleum.ru/max":
            route.fulfill(status=200, content_type="text/html", body=document)
            return
        if request.method == "POST":
            requests.append(request.url)
            scenario.scope.authorize("POST", request.url, request.post_data_json)
            payload = (
                {
                    "id": project,
                    "template": "max_miniapp",
                    "name": "QA catalog synthetic",
                    "current_snapshot_id": snapshot,
                }
                if request.url.endswith("/api/projects")
                else {"run_id": run, "mode": "build"}
            )
            from urllib.parse import urlsplit

            scenario._record_operation(
                urlsplit(request.url).path,
                201 if "id" in payload else 202,
                payload,
            )
            route.fulfill(
                status=201 if "id" in payload else 202,
                content_type="application/json",
                body=json.dumps(payload),
            )
            return
        route.abort()

    context.route("**/*", provider)
    page = context.new_page()
    page.set_default_timeout(1000)
    scenario._attach_page(page)
    try:
        if wrong_price:
            with pytest.raises(AssertionError):
                scenario._wizard(page)
            assert requests == [] and scenario.scope.project_id is None
        else:
            scenario._wizard(page)
            assert scenario.scope.project_id == project and scenario.run_id == run
            assert requests == [
                "https://yleum.ru/api/projects",
                f"https://yleum.ru/api/projects/{project}/prompt",
            ]
        exempt = learning.startswith("exempt")
        if exempt:
            expected = []
        elif learning == "resumed":
            expected = ["next", "next", "finish"]
        else:
            expected = ["start", "next", "next", "next", "finish"]
        assert page.evaluate("window.learningTrace") == expected
        assert ("first_visit_learning" in scenario.evidence.checks) == (not exempt)
        assert page.evaluate("[localStorage.length,sessionStorage.length]") == [0, 0]
        assert context.cookies() == []
    finally:
        context.close()


def test_real_browser_preview_screenshots_exclude_owner_header(offline_browser, tmp_path):
    source = module()
    scenario = object.__new__(source._CatalogScenario)
    scenario.config = config()
    scenario.scope = source.CatalogScope(config(), "QA catalog synthetic")
    project = str(uuid4())
    admit(scenario.scope, project)
    origin = "https://synthetic.dev.yleum.ru"
    scenario.scope.register_preview(
        origin + "/api/omnia/preview-session?expires=9999999999&signature=" + "a" * 43,
    )
    scenario.evidence = source.CatalogEvidence()
    scenario.api = SimpleNamespace(_deadline=time.monotonic() + 30)
    scenario.artifacts = tmp_path
    expected = [{"id": "one", "title": "QA Латте", "price": "210 ₽", "active": True}]
    scenario._catalog = lambda: {
        "content": expected,
        "config_version": 4,
        "preview": {"state": "applied", "confirmed_config_version": 4},
    }
    scenario._wait_catalog = lambda actual: source.assert_catalog(actual, expected)
    document = (
        '<meta charset="utf-8"><style>body{margin:0} .frame{overflow:hidden;width:100%}</style>'
        '<header style="height:80px">'
        "synthetic-private-owner@example.test</header>"
        '<div class="frame"><iframe data-testid="max-live-iframe" '
        f'style="width:390px;height:844px;border:0" src="{origin}/"></iframe></div>'
    )
    context = offline_browser.new_context(base_url="https://yleum.ru")
    scenario.context = context

    def provider(route):
        if route.request.url == f"https://yleum.ru/max/{project}/preview":
            body = document
        elif route.request.url == origin + "/" or route.request.url.startswith(
            origin + "/api/omnia/preview-session?"
        ):
            # This fixture owns the rendered document boundary, not the signed
            # gateway's 307 contract (covered by production_canary tests). Keep
            # every request virtual instead of following a synthetic DNS host.
            body = (
                '<meta charset="utf-8"><style>body{margin:0}</style><main>'
                f"<h1>{html.escape(expected[0]['title'])}</h1><p>210 ₽</p></main>"
            )
        else:
            route.abort()
            return
        route.fulfill(status=200, content_type="text/html", body=body)

    context.route("**/*", provider)
    try:
        scenario._preview(context.new_page(), expected, screenshots=True)
        from PIL import Image

        for width in (390, 768, 1440):
            with Image.open(tmp_path / f"preview-{width}.png") as picture:
                assert picture.height == 1000 and picture.width == width
        assert {path.name for path in tmp_path.iterdir()} == {
            "preview-390.png",
            "preview-768.png",
            "preview-1440.png",
        }
    finally:
        context.close()


@pytest.mark.parametrize("swapped", [False, True])
def test_real_browser_catalog_prices_belong_to_their_own_cards(offline_browser, swapped):
    source = module()
    page = offline_browser.new_page()
    expected = [
        {"id": "one", "title": "QA Латте", "price": "210 ₽", "active": True},
        {"id": "two", "title": "QA Чай", "price": "110 ₽", "active": True},
    ]
    prices = ("110 ₽", "210 ₽") if swapped else ("210 ₽", "110 ₽")
    page.set_content(
        "<main><div><article><h2>QA Латте</h2><span>"
        + prices[0]
        + "</span></article><article><h2>QA Чай</h2><span>"
        + prices[1]
        + "</span></article></div></main>"
    )
    try:
        if swapped:
            with pytest.raises(ValueError):
                source.assert_rendered_catalog(page, expected)
        else:
            source.assert_rendered_catalog(page, expected)
    finally:
        page.close()


def test_first_visit_learning_fails_closed_when_next_never_finishes(offline_browser):
    source = module()
    scenario = object.__new__(source._CatalogScenario)
    scenario.api = SimpleNamespace(_deadline=time.monotonic() + 10)
    scenario.evidence = source.CatalogEvidence()
    page = offline_browser.new_page()
    page.set_content(gated_studio_document(learning="endless"))
    try:
        with pytest.raises(ValueError, match="bounded step limit"):
            scenario._complete_first_visit_learning(page)
        assert page.evaluate("window.learningTrace") == ["start", *(["next"] * 8)]
        assert not page.evaluate("window.learningCompleted")
        assert not page.locator("#wizard").is_visible()
        assert scenario.evidence.checks == []
    finally:
        page.close()


@pytest.mark.parametrize("visible", [False, True])
def test_optional_panel_learning_can_still_be_dismissed_without_first_visit_evidence(
    offline_browser, visible,
):
    source = module()
    scenario = object.__new__(source._CatalogScenario)
    scenario.api = SimpleNamespace(_deadline=time.monotonic() + 10)
    scenario.evidence = source.CatalogEvidence()
    page = offline_browser.new_page()
    page.set_content(
        '<div role="dialog"><button aria-label="Закрыть обучение" '
        'onclick="this.parentElement.remove()">Закрыть обучение</button></div>'
        if visible else "<main>Панель без обучения</main>"
    )
    try:
        scenario._dismiss_learning(page)
        assert scenario.evidence.checks == []
        assert page.get_by_role("dialog").count() == 0
    finally:
        page.close()
