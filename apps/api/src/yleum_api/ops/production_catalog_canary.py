"""One disposable, normally authenticated production catalog browser acceptance.

No provider consent, payment, session minting, registration bypass or notification
is performed. Only the initial wizard may submit a generation. Browser state and
raw diagnostic strings never become artifacts.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
from playwright.sync_api import (
    APIRequestContext,
    Browser,
    BrowserContext,
    Error,
    FrameLocator,
    Page,
    Response,
    Route,
    WebSocketRoute,
    expect,
    sync_playwright,
)

from yleum_api.ops.production_canary import (
    COMPONENTS,
    CanaryConfig,
    CanaryFailure,
    ProductionCanary,
    validate_preview_url,
)

_ORIGIN = "https://yleum.ru"
_PREVIEW_SUFFIXES = {".dev.yleum.ru", ".dev2.yleum.ru"}
_ITEMS = (("QA Латте", "210 ₽"), ("QA Чай", "110 ₽"), ("QA Какао", "190 ₽"))
_RENAMED = "QA Латте обновлённый"
_ADDED = "QA Новый напиток"
_BRIEF = (
    "Каталог тестовой кофейни: покажи позиции и цены из каталога Yleum. "
    "Нужны каталог, простая заявка и состояния загрузки/ошибки. "
    "Без оплаты, платёжных интеграций, регистрации и внешних ресурсов.\n"
    + "\n".join(f"{title} — {price}" for title, price in _ITEMS)
)
_CODES = {
    "ok",
    "configuration_invalid",
    "browser_unavailable",
    "browser_failed",
    "scope_denied",
    "creation_unconfirmed",
    "generation_failed",
    "catalog_failed",
    "preview_failed",
    "usage_unsettled",
    "invariant_changed",
    "release_failed",
    "cleanup_failed",
    "timeout",
    "network_failed",
    "artifact_failed",
    "login_failed",
    "oauth_readiness_failed",
    "usage_unstable",
}
_OAUTH_ERRORS = {
    "integration_oauth_unavailable",
    "account_connection_replacement_requires_confirmation",
    "subscription_entitlement_required",
    "registration_verification_required",
    "not_found",
}


def _need(condition: object) -> None:
    if not condition:
        raise ValueError("catalog acceptance contract failed")


def _uuid(value: object) -> str:
    _need(isinstance(value, str))
    parsed = UUID(str(value))
    _need(str(parsed) == value)
    return str(parsed)


def validate_catalog_config(config: CanaryConfig) -> None:
    """Never send protected QA credentials to an arbitrary configured origin."""
    _need(config.base_url == _ORIGIN and config.email and config.password)
    suffixes = config.preview_host_suffixes or (config.preview_host_suffix,)
    _need(bool(suffixes) and set(suffixes) <= _PREVIEW_SUFFIXES)
    _need(set(config.expected_releases) == set(COMPONENTS))
    _need(all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in config.expected_releases.values()))
    _need(300 <= config.overall_timeout_seconds <= 3600 and 1 <= config.poll_seconds <= 30)


def catalog_signature(content: object) -> tuple[tuple[str, str, str, bool], ...]:
    if not isinstance(content, list) or len(content) > 100:
        raise ValueError("invalid catalog response")
    rows = []
    for item in content:
        _need(isinstance(item, dict))
        identity, title, price, active = (
            item.get(key) for key in ("id", "title", "price", "active")
        )
        _need(isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity))
        _need(isinstance(title, str) and isinstance(price, str) and type(active) is bool)
        rows.append((str(identity), str(title), str(price), bool(active)))
    _need(len({row[0] for row in rows}) == len(rows))
    return tuple(sorted(rows))


def assert_catalog(actual: object, expected: object) -> None:
    _need(catalog_signature(actual) == catalog_signature(expected))


def expected_added_item(
    initial: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> dict[str, Any]:
    """The saved row supplies its ID, never the values used as the oracle."""
    known = {row["id"] for row in initial}
    added = [row for row in actual if row.get("id") not in known]
    _need(len(added) == 1)
    expected = {"id": added[0].get("id"), "title": _ADDED, "price": "250 ₽", "active": True}
    assert_catalog(added, [expected])
    return expected


def assert_yookassa_available(catalog: Mapping[str, Any]) -> None:
    providers = catalog.get("providers")
    _need(isinstance(providers, list))
    assert isinstance(providers, list)
    matches = [
        item for item in providers if isinstance(item, dict) and item.get("key") == "yookassa"
    ]
    _need(
        len(matches) == 1
        and matches[0].get("oauth_supported") is True
        and matches[0].get("oauth_available") is True
    )


def validate_yookassa_authorization(payload: Mapping[str, Any]) -> None:
    """Validate the redirect in memory; no navigation, consent or token exchange."""
    value = payload.get("authorization_url")
    _need(isinstance(value, str) and len(value) <= 8192)
    parsed = urlsplit(str(value))
    _need(
        parsed.scheme == "https"
        and parsed.netloc == "yookassa.ru"
        and parsed.path == "/oauth/v2/authorize"
        and not parsed.fragment
    )
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    _need(set(query) == {"response_type", "client_id", "redirect_uri", "state"})
    _need(all(len(values) == 1 for values in query.values()))
    _need(query["response_type"] == ["code"])
    _need(
        query["redirect_uri"]
        == [
            "https://yleum.ru/api/integrations/oauth/yookassa/callback",
        ]
    )
    _need(
        bool(query["client_id"][0].strip())
        and not any(ord(char) < 32 for char in query["client_id"][0])
    )
    _need(re.fullmatch(r"[A-Za-z0-9_-]{20,256}", query["state"][0]))


def assert_rendered_catalog(surface: Page | FrameLocator, expected: list[dict[str, Any]]) -> None:
    """Bind each visible title to its own price, never to another card/grid price."""
    rows = catalog_signature(expected)
    for _identity, title, _price, active in rows:
        locator = surface.get_by_text(title, exact=True)
        if active:
            expect(locator.first).to_be_visible(timeout=15_000)
        else:
            expect(locator).to_have_count(0)
    visible = [{"title": title, "price": price} for _, title, price, active in rows if active]
    _need(bool(visible))
    paired = surface.locator("body").evaluate(
        r"""(body, rows) => {
      const norm = value => String(value ?? '').normalize('NFKC')
        .replace(/\s+/g, ' ').replace(/\s*₽/g, ' ₽').trim();
      const visible = el => !!(el.getClientRects().length)
        && getComputedStyle(el).visibility !== 'hidden';
      const elements = [...body.querySelectorAll('*')];
      if (elements.length > 20000) return false;
      const titles = new Map(rows.map(row => [norm(row.title),
        elements.filter(el => visible(el) && norm(el.innerText) === norm(row.title))]));
      return rows.every(row => {
        const title = norm(row.title), price = norm(row.price);
        if (!price) return false;
        const escaped = price.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const pricePattern = new RegExp('(?:^|\\s)' + escaped + '(?:$|\\s)');
        return titles.get(title).some(target => {
          for (let card = target.parentElement; card; card = card.parentElement) {
            const otherTitle = [...titles].some(([name, nodes]) => name !== title
              && nodes.some(node => card.contains(node)));
            if (otherTitle) return false;
            if (visible(card) && pricePattern.test(norm(card.innerText))) return true;
            if (card === body) break;
          }
          return false;
        });
      });
    }""",
        visible,
    )
    _need(paired is True)


def assert_initial_acceptance(payload: Mapping[str, Any], run: str, snapshot: str) -> None:
    """A completed checkpoint alone does not prove the current request was accepted."""
    _need(payload.get("id") == run and payload.get("status") == "completed")
    _need(payload.get("response_mode") == "build")
    acceptance = payload.get("acceptance")
    _need(
        isinstance(acceptance, dict)
        and acceptance.get("run_id") == run
        and acceptance.get("snapshot_id") == snapshot
        and acceptance.get("current_request_complete") is True
    )


def usage_signature(payload: Mapping[str, Any]) -> tuple[object, ...]:
    """Compare source calls/tokens, not price classification settling after a build."""
    run = _uuid(payload.get("run_id"))
    stages = payload.get("stages")
    if not isinstance(stages, list) or len(stages) > 100:
        raise ValueError("invalid usage response")
    rows = []
    for stage in stages:
        _need(isinstance(stage, dict))
        values = [stage.get(key) for key in ("calls", "tokens_in", "tokens_out", "retries")]
        _need(all(type(value) is int and value >= 0 for value in values))
        _need(isinstance(stage.get("id"), str))
        rows.append((stage["id"], *values))
    _need(len({row[0] for row in rows}) == len(rows))
    return (run, tuple(sorted(rows)))


@dataclass
class CatalogEvidence:
    """An allowlisted report: never arbitrary exception text, URLs or payloads."""

    code: str = "ok"
    cleanup: str = "unknown"
    checks: list[str] = field(default_factory=list)
    releases: dict[str, str] = field(default_factory=dict)
    screenshots: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0
    console_errors: int = 0
    page_errors: int = 0
    network_errors: int = 0
    project_id: str | None = None
    run_id: str | None = None
    snapshot_id: str | None = None
    oauth_error_code: str | None = None
    http_status: int | None = None

    def fail(self, code: str) -> None:
        _need(code in _CODES)
        if self.code == "ok":
            self.code = code

    def report(self) -> dict[str, object]:
        _need(self.code in _CODES and self.cleanup in {"ok", "failed", "unknown"})
        allowed = {
            "wizard",
            "initial_build",
            "catalog_update",
            "catalog_add",
            "catalog_hide",
            "catalog_delete",
            "refresh",
            "reconnect",
            "preview",
            "no_additional_ai",
            "first_visit_learning",
            "oauth_readiness",
        }
        _need(set(self.checks) <= allowed)
        _need(set(self.releases) <= set(COMPONENTS))
        _need(all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in self.releases.values()))
        _need(
            all(re.fullmatch(r"preview-(?:390|768|1440)\.png", name) for name in self.screenshots)
        )
        report: dict[str, object] = {
            "status": "success" if self.code == "ok" and self.cleanup == "ok" else "failure",
            "code": self.code,
            "cleanup": self.cleanup,
            "checks": list(self.checks),
            "releases": dict(self.releases),
            "screenshots": list(self.screenshots),
            "elapsed_seconds": round(max(0, self.elapsed_seconds), 3),
            "console_errors": self.console_errors,
            "page_errors": self.page_errors,
            "network_errors": self.network_errors,
        }
        # Synthetic resource IDs allow a bounded cleanup recovery. No owner,
        # account, merchant or session identity enters this artifact.
        for key in ("project_id", "run_id", "snapshot_id"):
            value = getattr(self, key)
            if value is not None:
                report[key] = _uuid(value)
        if self.oauth_error_code is not None:
            _need(self.oauth_error_code in _OAUTH_ERRORS)
            report["oauth_error_code"] = self.oauth_error_code
        if self.http_status is not None:
            _need(type(self.http_status) is int and 100 <= self.http_status <= 599)
            report["http_status"] = self.http_status
        return report

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "evidence.json").write_text(
            json.dumps(self.report(), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )


class CatalogScope:
    """Constrain browser and direct API traffic to this invocation's owned project."""

    def __init__(self, config: CanaryConfig, name: str) -> None:
        self.config = config
        self.name = name
        self.project_id: str | None = None
        self.preview_origins: set[str] = set()
        self.prompt_body: object = None
        self.prompt_requests = 0
        self.create_requests = 0
        self.frozen = False
        self.initial_content: list[dict[str, Any]] = []
        self.preview_bootstrap: str | None = None
        self.oauth_starts = 0

    def admit_project(self, payload: Mapping[str, Any]) -> None:
        _need(
            self.create_requests == 1
            and self.project_id is None
            and payload.get("name") == self.name
        )
        _need(payload.get("template") == "max_miniapp")
        self.project_id = _uuid(payload.get("id"))

    def freeze_generation(self) -> None:
        self.frozen = True

    def register_preview(self, url: str) -> None:
        _need(self.project_id is not None)
        for suffix in self.config.preview_host_suffixes or (self.config.preview_host_suffix,):
            try:
                parsed = validate_preview_url(url, suffix)
            except Exception:
                continue
            self.preview_origins.add(f"https://{parsed.netloc}")
            self.preview_bootstrap = url
            return
        raise ValueError("preview scope rejected")

    def authorize(
        self,
        method: str,
        url: str,
        payload: object = None,
        *,
        server_action: bool = False,
    ) -> None:
        parsed = urlsplit(url)
        _need(parsed.scheme == "https" and not parsed.username and not parsed.password)
        _need(parsed.port in {None, 443})
        origin = f"https://{parsed.netloc}"
        if origin in self.preview_origins:
            _need(
                method in {"GET", "HEAD"}
                or (method == "POST" and parsed.path == "/" and server_action)
            )
            return
        _need(origin == self.config.base_url)
        path = parsed.path
        owned = f"/api/projects/{self.project_id}" if self.project_id else None
        if path.startswith("/api/projects/"):
            _need(owned is not None and (path == owned or path.startswith(owned + "/")))
        if method in {"GET", "HEAD"}:
            return
        if method == "POST" and path in {"/api/auth/login", "/api/auth/logout"}:
            return
        if method == "POST" and path == "/api/projects":
            _need(self.create_requests == 0 and self.project_id is None)
            if not isinstance(payload, dict) or payload.get("name") != self.name:
                raise ValueError("invalid project creation")
            config = payload.get("max_config")
            if not isinstance(config, dict) or config.get("summary") != _BRIEF:
                raise ValueError("invalid initial catalog configuration")
            content = catalog_signature(config.get("content"))
            _need(sorted((row[1], row[2]) for row in content) == sorted(_ITEMS))
            _need(all(row[3] for row in content))
            self.initial_content = [dict(row) for row in config["content"]]
            self.create_requests += 1
            return
        if owned is None:
            raise ValueError("project ownership not established")
        if method == "POST" and path == owned + "/prompt":
            _need(not self.frozen and self.prompt_requests < 3)
            if not isinstance(payload, dict) or payload.get("skip_clarify") is not True:
                raise ValueError("invalid initial generation")
            _need(
                isinstance(payload.get("idempotency_key"), str)
                and len(payload["idempotency_key"]) >= 8
            )
            _need(self.prompt_body is None or self.prompt_body == payload)
            self.prompt_body = payload
            self.prompt_requests += 1
            return
        if method == "POST" and path == owned + "/app-integrations/yookassa/oauth/start":
            _need(self.oauth_starts == 0)
            self.oauth_starts += 1
            return
        _need(
            (method == "DELETE" and path == owned)
            or (method == "PATCH" and path == owned + "/max/content")
            or (
                method == "POST"
                and path
                in {
                    owned + "/runtime/start",
                    owned + "/max/preview-session",
                    owned + "/generation/cancel",
                }
            )
        )


class _BrowserTransport(httpx.BaseTransport):
    def __init__(self, context: APIRequestContext, scope: CatalogScope) -> None:
        self.context, self.scope = context, scope

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        self.scope.authorize(request.method, str(request.url), payload)
        timeout = request.extensions.get("timeout", {}).get("read", 30)
        try:
            response = self.context.fetch(
                str(request.url),
                method=request.method,
                headers={key: value for key, value in request.headers.items() if key != "cookie"},
                data=request.content or None,
                timeout=float(timeout or 30) * 1000,
                max_redirects=0,
                max_retries=0,
                fail_on_status_code=False,
            )
            try:
                body = response.body()
                _need(len(body) <= 2 * 1024 * 1024)
                return httpx.Response(
                    response.status, headers=response.headers, content=body, request=request
                )
            finally:
                response.dispose()
        except Error:
            raise httpx.TransportError("catalog browser API request failed") from None


def cleanup_catalog(api: ProductionCanary, project_id: str | None, *, logged_in: bool) -> str:
    """Use the existing owned-ID, bounded 202→GET→404 cleanup; never recover by name."""
    try:
        return "ok" if api._cleanup(project_id, logged_in=logged_in) is None else "failed"
    except BaseException:
        return "failed"


class _CatalogScenario:
    def __init__(
        self,
        config: CanaryConfig,
        browser: Browser,
        artifacts: Path,
        evidence: CatalogEvidence,
    ) -> None:
        self.config, self.browser, self.artifacts, self.evidence = (
            config,
            browser,
            artifacts,
            evidence,
        )
        self.scope = CatalogScope(config, "QA catalog " + uuid4().hex[:8])
        self.context = self._new_context()
        self.api = ProductionCanary(
            config, transport=_BrowserTransport(self.context.request, self.scope)
        )
        self.api._started_at = time.monotonic()
        self.api._deadline = self.api._started_at + config.overall_timeout_seconds
        self.run_id: str | None = None
        self.seed_snapshot: str | None = None
        self.logged_in = False
        self.authenticated_clients: list[httpx.Client] = []
        self.contexts = [self.context]
        self.phase = "browser_failed"

    def _timeout(self, seconds: float = 15) -> float:
        remaining = self.api._deadline - time.monotonic()
        if remaining <= 0:
            self.evidence.fail("timeout")
            raise ValueError("catalog time budget exhausted")
        return max(1, min(seconds, remaining) * 1000)

    def _new_context(self) -> BrowserContext:
        context = self.browser.new_context(
            base_url=self.config.base_url,
            viewport={"width": 1440, "height": 1000},
            service_workers="block",
            accept_downloads=False,
        )
        context.set_default_timeout(15_000)
        context.set_default_navigation_timeout(30_000)
        context.route("**/*", self._route)
        context.route_web_socket("**/*", self._socket)
        context.on("page", self._attach_page)
        return context

    def _attach_page(self, page: Page) -> None:
        page.on("pageerror", lambda _error: self._browser_error("page"))
        page.on(
            "console",
            lambda message: self._browser_error("console") if message.type == "error" else None,
        )
        page.on(
            "requestfailed",
            lambda request: (
                self._browser_error("network") if request.failure != "net::ERR_ABORTED" else None
            ),
        )
        page.on("response", self._response)

    def _browser_error(self, kind: str) -> None:
        if kind == "page":
            self.evidence.page_errors += 1
        elif kind == "console":
            self.evidence.console_errors += 1
        else:
            self.evidence.network_errors += 1

    def _route(self, route: Route) -> None:
        request = route.request
        try:
            path = urlsplit(request.url).path
            json_action = request.method == "POST" and (
                path == "/api/projects" or path.endswith("/prompt")
            )
            self.scope.authorize(
                request.method,
                request.url,
                request.post_data_json if json_action else None,
                server_action=bool(request.headers.get("next-action")),
            )
        except Exception:
            self.evidence.fail("scope_denied")
            self._abort(route)
            return
        # Capture the owned create response BEFORE the browser receives it and
        # immediately posts its starter prompt. A response listener reading JSON
        # is too late: Playwright can dispatch that next request reentrantly.
        observed = (
            request.method == "POST"
            and urlsplit(request.url).netloc == urlsplit(self.config.base_url).netloc
            and (path == "/api/projects" or path.endswith(("/prompt", "/max/preview-session")))
        )
        if observed:
            try:
                response = route.fetch(
                    timeout=self._timeout(30),
                    max_redirects=0,
                    max_retries=0,
                )
                try:
                    if 200 <= response.status < 300:
                        _need(len(response.body()) <= 2 * 1024 * 1024)
                        self._record_operation(path, response.status, response.json())
                    route.fulfill(response=response)
                finally:
                    response.dispose()
            except Exception:
                self.evidence.fail("browser_failed")
                self._abort(route)
            return
        try:
            route.continue_()
        except Error:
            self.evidence.fail("network_failed")

    @staticmethod
    def _abort(route: Route) -> None:
        try:
            route.abort("blockedbyclient")
        except Error:
            # Event-listener exceptions otherwise print raw private request URLs.
            pass

    def _socket(self, socket: WebSocketRoute) -> None:
        try:
            parsed = urlsplit(socket.url)
            _need(
                parsed.scheme == "wss"
                and parsed.netloc == urlsplit(self.config.base_url).netloc
                and (
                    (
                        getattr(self, "logged_in", False)
                        and parsed.path == "/api/ws/support"
                        and not parsed.query
                    )
                    or (
                        self.scope.project_id is not None
                        and parsed.path == f"/api/ws/projects/{self.scope.project_id}"
                    )
                )
            )
            if parsed.path == "/api/ws/support":
                upstream = socket.connect_to_server()

                def read_only(message: str | bytes) -> None:
                    try:
                        _need(len(message) <= 1024)
                        frame = json.loads(message)
                        _need(frame in ({"type": "subscribe"}, {"type": "ping"}))
                        upstream.send(message)
                    except Exception:
                        self.evidence.fail("scope_denied")
                        try:
                            socket.close(code=1008)
                        except Error:
                            pass

                socket.on_message(read_only)
            else:
                socket.connect_to_server()
        except Exception:
            self.evidence.fail("scope_denied")
            try:
                socket.close()
            except Error:
                pass

    def _response(self, response: Response) -> None:
        if response.status >= 400:
            self.evidence.network_errors += 1

    def _record_operation(self, path: str, status: int, payload: Any) -> None:
        _need(isinstance(payload, dict))
        if path == "/api/projects" and status == 201:
            self.scope.admit_project(payload)
            self.api._created_project_id = self.scope.project_id or ""
            self.evidence.project_id = self.scope.project_id
            self.seed_snapshot = _uuid(payload.get("current_snapshot_id"))
        elif path == f"/api/projects/{self.scope.project_id}/prompt" and status == 202:
            run_id = _uuid(payload.get("run_id"))
            _need(payload.get("mode") == "build" and self.run_id in {None, run_id})
            self.run_id = run_id
            self.evidence.run_id = run_id
        elif path == f"/api/projects/{self.scope.project_id}/max/preview-session":
            self.scope.register_preview(payload["url"])
        else:
            raise ValueError("unexpected operation response")

    def _json(self, method: str, path: str, payload: object = None) -> dict[str, Any]:
        result = self.api._request_json(method, path, json=payload)
        self._healthy_browser()
        return result

    def _healthy_browser(self) -> None:
        _need(self.evidence.code == "ok")
        _need(
            not (
                self.evidence.page_errors
                or self.evidence.console_errors
                or self.evidence.network_errors
            )
        )

    def _login(self) -> None:
        self.phase = "login_failed"
        self.api._stage = "login"
        # Even a malformed/lost response can have issued a server cookie. Own
        # logout for this context BEFORE request dispatch or JSON parsing; this
        # is cleanup responsibility, never authentication authority.
        if not hasattr(self, "authenticated_clients"):
            self.authenticated_clients = []
        if self.api._client not in self.authenticated_clients:
            self.authenticated_clients.append(self.api._client)
        self.api._request_json(
            "POST",
            "/api/auth/login",
            json={
                "email": self.config.email,
                "password": self.config.password,
            },
        )
        self.logged_in = True
        self._healthy_browser()

    def _dismiss_learning(self, page: Page) -> None:
        # Optional editor/panel tours do not gate creation. Dismiss only through
        # the public control, never by changing persisted state or auth guards.
        close = page.get_by_role("button", name="Закрыть обучение", exact=True)
        page.wait_for_timeout(min(800, self._timeout(1)))
        if close.is_visible():
            close.click(timeout=self._timeout(10))

    def _complete_first_visit_learning(self, page: Page) -> bool:
        close = page.get_by_role("button", name="Закрыть обучение", exact=True)
        if not close.is_visible():
            # Existing owners may legitimately have no mandatory tour.
            return False
        dialog = page.get_by_role("dialog").filter(has=close)
        start = dialog.get_by_role("button", name="Начать обучение", exact=True)
        if start.is_visible():
            start.click(timeout=self._timeout(10))
        # The cabinet currently has four steps. Bound UI progress so a broken
        # tour cannot loop forever or fall through to a paid creation attempt.
        for _ in range(8):
            finish = dialog.get_by_role("button", name="Завершить", exact=True)
            if finish.is_visible():
                finish.click(timeout=self._timeout(10))
                expect(close).not_to_be_visible(timeout=self._timeout(10))
                self.evidence.checks.append("first_visit_learning")
                return True
            next_step = dialog.get_by_role("button", name="Далее", exact=True)
            expect(next_step).to_be_visible(timeout=self._timeout(10))
            next_step.click(timeout=self._timeout(10))
        raise ValueError("first-visit learning exceeded its bounded step limit")

    def _wizard(self, page: Page) -> None:
        self.phase = "creation_unconfirmed"
        page.goto("/max", wait_until="domcontentloaded", timeout=self._timeout(30))
        expect(page.locator('[data-max-studio][data-learning-ready="true"]')).to_be_visible(
            timeout=self._timeout(15)
        )
        create = page.get_by_role("button", name="Создать приложение", exact=True)
        expect(create).to_be_visible()
        # Allow the automatic first-visit entry, then handle a dismissed/resumed
        # mandatory tour which opens only when Create is pressed.
        page.wait_for_timeout(min(800, self._timeout(1)))
        self._complete_first_visit_learning(page)
        create.click(timeout=self._timeout(10))
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible(timeout=self._timeout(10))
        if self._complete_first_visit_learning(page):
            create.click(timeout=self._timeout(10))
        dialog.locator("#max-project-name").fill(self.scope.name)
        dialog.locator("#max-project-idea").fill(_BRIEF)
        dialog.get_by_role("button", name="Каталог", exact=False).click()
        for _ in range(3):
            dialog.get_by_role("button", name="Далее", exact=True).click()
        for index, (title, price) in enumerate(_ITEMS, 1):
            expect(dialog.get_by_label(f"Название позиции {index}", exact=True)).to_have_value(
                title
            )
            expect(dialog.get_by_label(f"Цена позиции {index}", exact=True)).to_have_value(price)
        dialog.get_by_role("button", name="Создать проект", exact=True).click()
        deadline = time.monotonic() + min(60, self._timeout(60) / 1000)
        while (
            self.scope.project_id is None or self.run_id is None
        ) and time.monotonic() < deadline:
            self._healthy_browser()
            page.wait_for_timeout(200)
        _need(self.scope.project_id and self.run_id and self.seed_snapshot)
        self.evidence.checks.append("wizard")

    def _catalog(self) -> dict[str, Any]:
        return self._json("GET", f"/api/projects/{self.scope.project_id}/max/content")

    def _wait_catalog(self, expected: list[dict[str, Any]]) -> None:
        deadline = time.monotonic() + min(30, self._timeout(30) / 1000)
        while True:
            actual = self._catalog()
            try:
                assert_catalog(actual.get("content"), expected)
                return
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

    def _stable_usage(self, page: Page) -> tuple[object, ...]:
        self.phase = "usage_unstable"
        deadline = time.monotonic() + min(90, self._timeout(90) / 1000)
        previous: tuple[object, ...] | None = None
        stable = 0
        while time.monotonic() < deadline:
            usage = self._json("GET", f"/api/projects/{self.scope.project_id}/max/usage")
            signature = usage_signature(usage)
            _need(signature[0] == self.run_id)
            stable = stable + 1 if signature == previous else 1
            if stable >= 3:
                return signature
            previous = signature
            page.wait_for_timeout(min(2000, self._timeout(2)))
        raise ValueError("source call baseline did not stabilize")

    def _save_editor(self, page: Page, *, title: str, price: str) -> None:
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Название элемента 1", exact=True).fill(title)
        dialog.get_by_label("Цена или подпись элемента 1", exact=True).fill(price)
        dialog.get_by_role("button", name="Сохранить позицию", exact=True).click()
        expect(dialog).not_to_be_visible(timeout=self._timeout(30))

    def _edit_catalog(self, page: Page, initial: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.phase = "catalog_failed"
        project = self.scope.project_id
        page.goto(
            f"/max/{project}/catalog", wait_until="domcontentloaded", timeout=self._timeout(30)
        )
        card = page.locator("article.max-catalog-card").filter(
            has=page.get_by_role("heading", name=_ITEMS[0][0], exact=True),
        )
        expect(card).to_be_visible(timeout=self._timeout(30))
        self._dismiss_learning(page)
        card.get_by_role("button", name="Изменить", exact=True).click()
        self._save_editor(page, title=_RENAMED, price="225 ₽")
        expected = [
            {
                **row,
                **({"title": _RENAMED, "price": "225 ₽"} if row["title"] == _ITEMS[0][0] else {}),
            }
            for row in initial
        ]
        self._wait_catalog(expected)
        self.evidence.checks.append("catalog_update")
        page.get_by_role("button", name="Добавить позицию", exact=True).click()
        self._save_editor(page, title=_ADDED, price="250 ₽")
        added = self._catalog()["content"]
        expected = [*expected, expected_added_item(initial, added)]
        self._wait_catalog(expected)
        self.evidence.checks.append("catalog_add")
        page.get_by_role("button", name=f"Скрыть {_ITEMS[1][0]}", exact=True).click()
        save = page.get_by_role("button", name="Сохранить изменения", exact=True)
        expect(save).to_be_visible(timeout=self._timeout(15))
        save.click()
        expected = [
            {**row, **({"active": False} if row["title"] == _ITEMS[1][0] else {})}
            for row in expected
        ]
        self._wait_catalog(expected)
        self.evidence.checks.append("catalog_hide")
        # Delete one exact item whose initial live rendering was already proved.
        # The owner UI intentionally exposes hide, not a delete control.
        current = self._catalog()
        removed = [row for row in initial if row["title"] == _ITEMS[2][0]]
        _need(len(removed) == 1)
        expected = [row for row in expected if row["id"] != removed[0]["id"]]
        self._json(
            "PATCH",
            f"/api/projects/{project}/max/content",
            {
                "expected_config_version": current["config_version"],
                "content": expected,
            },
        )
        self._wait_catalog(expected)
        self.evidence.checks.append("catalog_delete")
        page.reload(wait_until="domcontentloaded", timeout=self._timeout(30))
        expect(page.get_by_role("heading", name=_RENAMED, exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name=_ADDED, exact=True)).to_be_visible()
        self._wait_catalog(expected)
        self.evidence.checks.append("refresh")
        return expected

    def _preview(self, page: Page, expected: list[dict[str, Any]], *, screenshots: bool) -> None:
        self.phase = "preview_failed"
        page.goto(
            f"/max/{self.scope.project_id}/preview",
            wait_until="domcontentloaded",
            timeout=self._timeout(30),
        )
        iframe = page.get_by_test_id("max-live-iframe")
        expect(iframe).to_be_visible(timeout=self._timeout(60))
        self._dismiss_learning(page)
        _need(
            any(
                (iframe.get_attribute("src") or "").startswith(origin + "/")
                for origin in self.scope.preview_origins
            )
        )
        frame = iframe.content_frame
        assert_rendered_catalog(frame, expected)
        for title, _price in _ITEMS:
            if title not in {row["title"] for row in expected}:
                expect(frame.get_by_text(title, exact=True)).to_have_count(0)
        page.reload(wait_until="domcontentloaded", timeout=self._timeout(30))
        assert_rendered_catalog(page.get_by_test_id("max-live-iframe").content_frame, expected)
        self._responsive_preview(page, expected, screenshots=screenshots)
        current = self._catalog()
        _need(current.get("preview", {}).get("state") == "applied")
        _need(current["preview"].get("confirmed_config_version") == current.get("config_version"))
        self._wait_catalog(expected)

    def _responsive_preview(
        self,
        owner: Page,
        expected: list[dict[str, Any]],
        *,
        screenshots: bool,
    ) -> None:
        # The editor deliberately scales a fixed 390px iframe. A separate page
        # in the SAME normally authenticated context proves actual breakpoints.
        bootstrap = self.scope.preview_bootstrap
        _need(isinstance(bootstrap, str))
        assert isinstance(bootstrap, str)
        self.scope.register_preview(bootstrap)
        product = self.context.new_page()
        try:
            product.goto(bootstrap, wait_until="domcontentloaded", timeout=self._timeout(30))
            for width in (390, 768, 1440):
                self._timeout()
                owner.set_viewport_size({"width": width, "height": 1000})
                _need(owner.evaluate("window.innerWidth") == width)
                _need(
                    owner.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
                )
                product.set_viewport_size({"width": width, "height": 1000})
                _need(product.evaluate("window.innerWidth") == width)
                _need(
                    product.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth + 1"
                    )
                )
                assert_rendered_catalog(product, expected)
                if screenshots:
                    name = f"preview-{width}.png"
                    self.artifacts.mkdir(parents=True, exist_ok=True)
                    product.screenshot(
                        path=self.artifacts / name,
                        animations="disabled",
                        timeout=self._timeout(15),
                    )
                    self.evidence.screenshots.append(name)
        finally:
            product.close()

    def _oauth_readiness(self) -> None:
        self.phase = "oauth_readiness_failed"
        base = f"/api/projects/{self.scope.project_id}/app-integrations"
        assert_yookassa_available(self._json("GET", base))
        response = self.api._client.request(
            "POST",
            base + "/yookassa/oauth/start",
            timeout=self._timeout(15) / 1000,
        )
        self.evidence.http_status = response.status_code
        if not 200 <= response.status_code < 300:
            try:
                code = response.json().get("error", {}).get("code")
                if isinstance(code, str) and code in _OAUTH_ERRORS:
                    self.evidence.oauth_error_code = code
            except (ValueError, TypeError, AttributeError):
                pass
            raise ValueError("OAuth readiness denied")
        payload = response.json()
        _need(isinstance(payload, dict))
        validate_yookassa_authorization(payload)
        self._healthy_browser()
        self.evidence.checks.append("oauth_readiness")

    def run(self) -> CatalogEvidence:
        page = self.context.new_page()
        try:
            self.phase = "release_failed"
            self.api._stage = "release_health"
            self.evidence.releases = self.api._assert_release_health()
            self._login()
            self._wizard(page)
            project, run = self.scope.project_id, self.run_id
            _need(project and run and self.seed_snapshot)
            assert project is not None and run is not None and self.seed_snapshot is not None
            self.phase = "generation_failed"
            self.api._stage = "build"
            completed = self.api._poll_generation(project, run)
            self.scope.freeze_generation()
            snapshot = self.api._assert_new_snapshot(project, self.seed_snapshot)
            assert_initial_acceptance(completed, run, snapshot)
            self.evidence.snapshot_id = snapshot
            self.evidence.checks.append("initial_build")
            self.api._start_runtime(project)
            current = self._catalog()["content"]
            assert_catalog(current, self.scope.initial_content)
            baseline = self._stable_usage(page)
            self._preview(page, current, screenshots=False)
            expected = self._edit_catalog(page, current)
            self._preview(page, expected, screenshots=True)
            self.evidence.checks.append("preview")
            self._healthy_browser()
            # A second context obtains a NEW server session through normal
            # login. No cookies/storageState are copied or written to disk.
            page = self._reconnect()
            self._preview(page, expected, screenshots=False)
            self.evidence.checks.append("reconnect")
            self.phase = "invariant_changed"
            _need(self._json("GET", f"/api/projects/{project}")["current_snapshot_id"] == snapshot)
            _need(self._json("GET", f"/api/projects/{project}/generation")["id"] == run)
            _need(
                usage_signature(self._json("GET", f"/api/projects/{project}/max/usage")) == baseline
            )
            self.evidence.checks.append("no_additional_ai")
            self._oauth_readiness()
            self.phase = "release_failed"
            self.api._assert_release_health(baseline=self.evidence.releases)
            self._healthy_browser()
        except BaseException as exc:
            if isinstance(exc, CanaryFailure):
                self.evidence.http_status = exc.http_status
            self.evidence.fail(self.phase)
        finally:
            # Recover only an ID proved by this invocation's successful create
            # response; an ambiguous create never triggers a list/name search.
            if self.scope.project_id is not None:
                self.api._created_project_id = self.scope.project_id
            self.evidence.cleanup = cleanup_catalog(
                self.api,
                self.scope.project_id,
                logged_in=(
                    self.logged_in or self.api._client in getattr(self, "authenticated_clients", [])
                ),
            )
            if self.scope.create_requests and self.scope.project_id is None:
                # The POST may have succeeded without a usable response. Never
                # claim project cleanup from a successful logout alone.
                self.evidence.cleanup = "unknown"
            if self.evidence.cleanup != "ok":
                self.evidence.fail("cleanup_failed")
            # A second normal login can issue a session before an adjacent
            # browser check fails. Log out every proved session while its own
            # BrowserContext still owns the cookie; never copy/extract tokens.
            self._logout_other_sessions()
            for context in self.contexts:
                try:
                    context.close()
                except Error:
                    self.evidence.fail("cleanup_failed")
            self.evidence.elapsed_seconds = time.monotonic() - self.api._started_at
        return self.evidence

    def _reconnect(self) -> Page:
        old_client = self.api._client
        self.context = self._new_context()
        self.contexts.append(self.context)
        self.api._client = self.api.new_client(
            transport=_BrowserTransport(self.context.request, self.scope),
        )
        try:
            self._login()
        except BaseException:
            if self.api._client not in self.authenticated_clients:
                self.api._client.close()
            self.api._client = old_client
            raise
        return self.context.new_page()

    def _logout_other_sessions(self) -> None:
        for client in getattr(self, "authenticated_clients", []):
            if client is self.api._client:
                continue  # Existing cleanup already logged out this session.
            try:
                _need(client.post("/api/auth/logout", timeout=10).status_code == 204)
            except Exception:
                self.evidence.cleanup = "failed"
                self.evidence.fail("cleanup_failed")
            finally:
                client.close()


def run_catalog_canary(config: CanaryConfig, artifacts: Path) -> CatalogEvidence:
    evidence = CatalogEvidence()
    try:
        validate_catalog_config(config)
    except (ValueError, TypeError):
        evidence.fail("configuration_invalid")
        return evidence
    try:
        _need(version("playwright") == "1.60.0")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                evidence = _CatalogScenario(config, browser, artifacts, evidence).run()
            finally:
                browser.close()
    except BaseException:
        evidence.fail("browser_unavailable")
    return evidence
