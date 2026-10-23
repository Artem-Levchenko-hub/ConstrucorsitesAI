"""Trusted language-neutral MAX preview gateway, never mounted into project code."""

from __future__ import annotations

import base64
import hmac
import http.client
import http.cookies
import http.server
import json
import os
import re
import secrets
import time
from collections.abc import Iterable
from html import escape
from typing import Any, cast
from urllib.parse import quote, unquote, urlsplit

_RESERVED = ("/api/omnia", "/api/max", "/__omnia", "/auth")
_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

_PUBLIC_ANONYMOUS = {
    "/api/max/session", "/api/max/webhook", "/api/omnia/config", "/api/omnia/health",
}
_SESSION_COOKIE = "__Host-max_session"
_EMBEDDED_COOKIES = ("__Host-max_session_embedded", "__Host-max_session_partitioned")
_PUBLIC_FRAMING = "frame-ancestors 'self' https://web.max.ru https://max.ru"
_CACHE_POLICY = {
    "cache-control", "cdn-cache-control", "cloudflare-cdn-cache-control",
    "surrogate-control", "expires", "pragma", "age",
}
# Адрес кабинета приходит в конфигурации шлюза: он менялся при переименовании,
# а вшитый адрес означает, что после переезда браузер перестаёт показывать
# превью владельцу. Старые шлюзы без этого ключа продолжают работать на
# запасном адресе, пока контроллер не пересоздаст их с новой конфигурацией.
_FALLBACK_OWNER_ORIGINS = ("https://constructor.lead-generator.ru",)
_ORIGIN_RE = re.compile(r"^https://[a-z0-9.-]{1,253}(:\d{1,5})?$")


def _validated_owner_origins(origins: object) -> list[str]:
    """Preserve validated controller origin spelling for the framing contract."""
    allowed: list[str] = []
    for origin in origins if isinstance(origins, list) else []:
        if not isinstance(origin, str) or not _ORIGIN_RE.fullmatch(origin):
            continue
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError:
            continue
        if port == 0:
            continue
        if origin not in allowed:
            allowed.append(origin)
    return allowed or list(_FALLBACK_OWNER_ORIGINS)


def owner_origins(origins: object) -> list[str]:
    """Only canonical configured HTTPS origins may receive the owner bridge."""
    allowed: list[str] = []
    for origin in _validated_owner_origins(origins):
        parsed = urlsplit(origin)
        port = parsed.port
        # Browser MessageEvent.origin omits HTTPS's default port.
        canonical = f"https://{parsed.hostname}" + (f":{port}" if port not in {None, 443} else "")
        if canonical not in allowed:
            allowed.append(canonical)
    return allowed


def owner_framing(origins: object) -> str:
    """Политика встраивания превью владельца для перечисленных кабинетов."""
    return "frame-ancestors 'self' " + " ".join(_validated_owner_origins(origins))


_OWNER_RECOVERY_SCRIPT = """
(() => {
  const config = __OWNER_RECOVERY_CONFIG__;
  const status = document.getElementById('status');
  let active = true;
  let cancelBridge = null;
  const controllers = new Set();
  const unavailable = () => {
    status.textContent = 'Откройте превью в редакторе проекта.';
  };
  window.addEventListener('pagehide', () => {
    active = false;
    cancelBridge?.();
    for (const controller of controllers) controller.abort();
  }, {once: true});
  const capability = () => new Promise(resolve => {
    const nonce = Array.from(window.crypto.getRandomValues(new Uint8Array(16)),
      value => value.toString(16).padStart(2, '0')).join('');
    let settled = false;
    const finish = url => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener('message', listener);
      cancelBridge = null;
      resolve(url);
    };
    const listener = event => {
      if (!active || settled || event.source !== window.parent ||
          !config.origins.includes(event.origin) ||
          event.data?.type !== 'omnia:preview-session:result' ||
          event.data?.nonce !== nonce) return;
      try {
        const url = new URL(event.data.url);
        finish(!event.data.error && url.protocol === 'https:' &&
          url.origin === location.origin && url.pathname === '/api/omnia/preview-session' &&
          !url.username && !url.password && !url.hash ? url.href : null);
      } catch (_) { finish(null); }
    };
    const timer = setTimeout(() => finish(null), 10000);
    cancelBridge = () => finish(null);
    window.addEventListener('message', listener);
    try {
      for (const origin of config.origins) {
        window.parent.postMessage({type: 'omnia:preview-session:renew', nonce}, origin);
      }
    } catch (_) { finish(null); }
  });
  async function evidence(url, headers = {}) {
    const controller = new AbortController();
    controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(url, {credentials: 'include', cache: 'no-store',
        redirect: 'error', headers, signal: controller.signal});
      if (!response.ok) throw new Error('Session unavailable');
      return await response.json();
    } finally {
      clearTimeout(timer);
      controllers.delete(controller);
    }
  }
  async function recover() {
    if (window.parent === window) { unavailable(); return; }
    try {
      const url = await capability();
      if (!active || !url) { unavailable(); return; }
      const bootstrap = await evidence(url, {Accept: 'application/json'});
      if (!active || bootstrap?.mode !== 'preview' || bootstrap?.user?.id !== 'preview') {
        unavailable(); return;
      }
      const identity = await evidence('/__omnia/identity');
      if (!active || identity?.user_id !== 'preview' || identity?.project_id !== config.project ||
          identity?.epoch !== config.epoch) { unavailable(); return; }
      // Reload only the original document after the gateway proves its cookie.
      // No product code or business request has run in this trusted shell.
      location.reload();
    } catch (_) { unavailable(); }
  }
  void recover();
})();
"""


_BOOTSTRAP_SCRIPT = """
(() => {
  const message = document.getElementById('status');
  const retry = document.getElementById('retry');
  let busy = false;
  async function fetchWithDeadline(url, options, timeout) {
    // AbortSignal.timeout is absent in some embedded WebViews. AbortController
    // gives those clients the same bounded request rather than a JS exception.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      return await fetch(url, {...options, signal: controller.signal});
    } finally {
      clearTimeout(timer);
    }
  }
  async function authenticate() {
    if (busy) return;
    busy = true;
    retry.disabled = true;
    message.textContent = 'Проверяем запуск из MAX…';
    try {
      if (!window.WebApp) {
        await new Promise((resolve, reject) => {
          const script = document.createElement('script');
          const timer = setTimeout(() => reject(new Error('MAX SDK timeout')), 10000);
          script.src = 'https://st.max.ru/js/max-web-app.js';
          script.onload = () => { clearTimeout(timer); resolve(); };
          script.onerror = () => { clearTimeout(timer); reject(new Error('MAX SDK unavailable')); };
          document.head.appendChild(script);
        });
      }
      const app = window.WebApp;
      if (!app || !app.initData) {
        message.textContent = 'Откройте приложение из чата с ботом в MAX.';
        return;
      }
      app.ready?.();
      app.expand?.();
      const session = await fetchWithDeadline('/api/max/session', {
        method: 'POST', credentials: 'include', cache: 'no-store',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({initData: app.initData})
      }, 60000);
      if (!session.ok) throw new Error('MAX authentication failed');
      const identity = await fetchWithDeadline('/__omnia/identity', {
        credentials: 'include', cache: 'no-store'
      }, 10000);
      if (!identity.ok) {
        message.textContent = 'Не удалось сохранить защищённую сессию. '
          + 'Закройте приложение и откройте снова.';
        return;
      }
      // MAX launch data lives in the URL fragment. Replacing that same URL is
      // only a same-document navigation; reload to enter the protected app.
      location.reload();
    } catch (_) {
      message.textContent = 'Не удалось войти через MAX. Проверьте соединение и повторите.';
    } finally {
      busy = false;
      retry.disabled = false;
    }
  }
  retry.addEventListener('click', authenticate);
  void authenticate();
})();
"""


def verified_user(value: str, secret: str) -> dict[str, Any] | None:
    try:
        encoded, provided = value.split(".")
        expected = base64.urlsafe_b64encode(
            hmac.digest(secret.encode(), encoded.encode(), "sha256")
        )
        if not hmac.compare_digest(provided, expected.decode().rstrip("=")):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["id"])
            or type(payload.get("expiresAt")) is not int
            or payload["expiresAt"] < int(time.time())
        ):
            return None
        return {key: value for key, value in payload.items() if key != "expiresAt"}
    except (ValueError, TypeError, UnicodeError):
        return None


def authenticated_session(
    raw_cookie: str, secret: str, *, public: bool,
) -> tuple[dict[str, Any] | None, str]:
    cookies = http.cookies.SimpleCookie()
    try:
        cookies.load(raw_cookie)
    except http.cookies.CookieError:
        return None, ""
    candidates = []
    names = (_SESSION_COOKIE, *_EMBEDDED_COOKIES) if public else (_SESSION_COOKIE,)
    for name in names:
        session = cookies.get(name)
        user = verified_user(session.value, secret) if session else None
        # Public MAX identities are numeric. Never promote the owner preview user.
        if session is not None and user is not None and (
            not public or re.fullmatch(r"[0-9]+", user["id"])
        ):
            candidates.append((user, session.value))
    if not candidates or len({user["id"] for user, _ in candidates}) != 1:
        # Conflicting accounts must reauthenticate; the login endpoint stays open.
        return None, ""
    return candidates[0]


def embedded_session_cookies(header: str, secret: str) -> list[str]:
    """Mirror only a verified, host-only core login cookie; never extend its lifetime."""
    cookies = http.cookies.SimpleCookie()
    try:
        cookies.load(header)
        session = cookies.get(_SESSION_COOKIE)
        if session is None or len(cookies) != 1:
            return []
        user = verified_user(session.value, secret)
        if (
            user is None or not re.fullmatch(r"[0-9]+", user["id"])
            or not session["secure"] or not session["httponly"]
            or session["path"] != "/" or session["domain"]
        ):
            return []
        max_age = min(int(session["max-age"]), 86400)
        if max_age <= 0:
            return []
        # Keep the original Lax cookie for first-party clients. Older webviews
        # may reject Partitioned; newer embedded browsers may require it.
        attributes = f"; Path=/; Secure; HttpOnly; SameSite=None; Max-Age={max_age}"
        return [
            f"{_EMBEDDED_COOKIES[0]}={session.value}{attributes}",
            f"{_EMBEDDED_COOKIES[1]}={session.value}{attributes}; Partitioned",
        ]
    except (http.cookies.CookieError, ValueError):
        return []


def canonical_request_path(raw: str) -> str | None:
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if (
        not path.startswith("/")
        or "//" in path
        or "\\" in path
        or "%" in path
        or any(part in {".", ".."} for part in path.split("/"))
        or any(ord(char) < 32 for char in path)
    ):
        return None
    return path


def route_port(path: str, routes: list[dict[str, Any]]) -> int | None:
    canonical = canonical_request_path(path)
    if canonical is None or any(
        canonical == item or canonical.startswith(item + "/") for item in _RESERVED
    ):
        return None
    for route in sorted(routes, key=lambda item: len(item["path"]), reverse=True):
        prefix = route["path"]
        if prefix == "/" or canonical == prefix or canonical.startswith(prefix + "/"):
            port = route["port"]
            return port if type(port) is int else None
    return None


def managed_headers(
    headers: Iterable[tuple[str, str]], *, actor_precondition: bool = False,
) -> dict[str, str]:
    """Keep a caller's actor expectation, never caller-supplied identity claims."""
    forwarded = {}
    for key, value in headers:
        name = key.casefold()
        if name in _HOP:
            continue
        if name.startswith("x-omnia-") and not (actor_precondition and name == "x-omnia-actor"):
            continue
        forwarded[key] = value
    return forwarded


def product_headers(
    headers: dict[str, str], *, project_id: str, epoch: int, user: dict[str, Any],
    origin: object = None,
) -> dict[str, str]:
    clean = {
        key: value
        for key, value in headers.items()
        if not key.casefold().startswith("x-omnia-")
        and key.casefold() not in (_HOP | {"cookie", "authorization", "forwarded"})
        and not key.casefold().startswith("x-forwarded-")
    }
    clean.update(
        {
            "X-Omnia-User-ID": user["id"],
            "X-Omnia-Project-ID": project_id,
            "X-Omnia-Session-Epoch": str(epoch),
        }
    )
    if isinstance(origin, str) and _ORIGIN_RE.fullmatch(origin):
        # Next validates Server Actions against the forwarded authority. Removing
        # caller forwarding headers must not replace it with the container IP.
        # Only controller configuration selects this host; preserve request Origin
        # so a cross-origin action still fails Next's independent CSRF check.
        authority = urlsplit(origin).netloc
        clean.update({"Host": authority, "X-Forwarded-Host": authority,
                      "X-Forwarded-Proto": "https"})
    return clean


def response_headers(
    headers: Iterable[tuple[str, str]], *, managed: bool, personal: bool,
) -> list[tuple[str, str]]:
    """Generated responses cannot opt a verified user's dynamic data into caching."""
    clean = [
        (key, value) for key, value in headers
        if key.casefold() not in _HOP
        and (managed or key.casefold() != "set-cookie")
        and (not personal or key.casefold() not in _CACHE_POLICY)
    ]
    if personal:
        clean.append(("Cache-Control", "private, no-store"))
    return clean


class BoundaryServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    config: dict[str, Any]


class BoundaryHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        # No signed bootstrap URL, cookie, body, or project credentials in logs.
        pass

    def end_headers(self) -> None:
        config = cast(BoundaryServer, self.server).config
        public = config.get("public_mode") is True
        # Origin checks stop CSRF, but not clicks inside a hostile iframe. Owner
        # drafts embed only in Studio; public releases embed only in MAX clients.
        # A separate CSP intersects, never weakens any upstream policy.
        self.send_header(
            "Content-Security-Policy",
            _PUBLIC_FRAMING if public else owner_framing(config.get("owner_origins")),
        )
        super().end_headers()

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def do_PUT(self) -> None:
        self._forward()

    def do_PATCH(self) -> None:
        self._forward()

    def do_DELETE(self) -> None:
        self._forward()

    def do_HEAD(self) -> None:
        self._forward()

    def _reply(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _bootstrap(self) -> None:
        nonce = secrets.token_urlsafe(24)
        body = (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<title>Вход через MAX</title></head>'
            '<body style="margin:0;font-family:system-ui;background:#f7f8fa;color:#17202a">'
            '<main style="max-width:360px;margin:20vh auto;padding:24px;text-align:center">'
            '<h1>Вход через MAX</h1><p id="status" role="status">Подключаем приложение…</p>'
            '<button id="retry" type="button" style="min-height:48px;padding:12px 24px">'
            'Повторить</button></main>'
            f'<script nonce="{nonce}">{_BOOTSTRAP_SCRIPT}</script></body></html>'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; script-src 'nonce-{nonce}' https://st.max.ru; "
            "connect-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _private_document(self, path: str, length: int) -> bool:
        if (
            self.command != "GET" or length
            or self.headers.get("Sec-Fetch-Dest", "") not in {"", "document", "iframe", "frame"}
            or self.headers.get("Sec-Fetch-Mode", "") not in {"", "navigate"}
            or any(name in self.headers for name in (
                "RSC", "Next-Action", "Next-Router-State-Tree", "Next-Router-Prefetch",
            ))
            or any(path == prefix or path.startswith(prefix + "/") for prefix in (
                "/api", "/_next", "/__omnia", "/auth",
            ))
        ):
            return False
        for media in self.headers.get("Accept", "").split(","):
            kind, *parameters = media.strip().lower().split(";")
            if kind.strip() != "text/html":
                continue
            try:
                qualities = [float(value.strip()[2:]) for value in parameters
                             if value.strip().startswith("q=")]
                if not qualities or (len(qualities) == 1 and 0 < qualities[0] <= 1):
                    return True
            except ValueError:
                pass
        return False

    def _owner_recovery(self) -> None:
        config = cast(BoundaryServer, self.server).config
        origins = owner_origins(config.get("owner_origins"))
        owner_link = origins[0] + "/max/" + quote(str(config["project_id"]), safe="") + "/preview"
        nonce = secrets.token_urlsafe(24)
        script_config = json.dumps({
            "origins": origins, "project": str(config["project_id"]), "epoch": config["epoch"],
        }).replace("<", "\\u003c")
        script = _OWNER_RECOVERY_SCRIPT.replace("__OWNER_RECOVERY_CONFIG__", script_config)
        body = (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Превью проекта</title></head><body><main>'
            '<h1>Превью проекта</h1><p id="status" role="status">Подключаем превью…</p>'
            f'<a href="{escape(owner_link, quote=True)}" target="_top" rel="noreferrer">'
            'Открыть превью в редакторе</a></main>'
            f'<script nonce="{nonce}">{script}</script></body></html>'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self) -> None:
        config = cast(BoundaryServer, self.server).config
        path = canonical_request_path(self.path)
        # BaseHTTPRequestHandler collapses leading // before assigning self.path.
        # Validate the original request target too, before choosing any shell.
        raw_target = self.requestline.split()[1]
        if (
            path is None or canonical_request_path(raw_target) != path
            or self.headers.get("Transfer-Encoding")
        ):
            return self._reply(400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._reply(400)
        if not 0 <= length <= 8 * 1024**2:
            return self._reply(413)
        public = config.get("public_mode") is True
        preview_path = "/api/omnia/preview-session"
        if public and (path == preview_path or path.startswith(preview_path + "/")):
            return self._reply(404)
        user, session_value = authenticated_session(
            self.headers.get("Cookie", ""), config["secret"], public=public,
        )
        if (
            public and self.command not in {"GET", "HEAD", "OPTIONS"}
            and path != "/api/max/webhook"
            and (user is not None or path == "/api/max/session")
        ):
            # SameSite=None needs an explicit CSRF boundary. Never trust Host or
            # forwarding headers, null/missing origins, or duplicate Origin fields.
            origin = config.get("public_origin", "")
            if not origin.startswith("https://") or self.headers.get_all("Origin") != [origin]:
                return self._reply(403, b"Same-origin request required")
        legal_page = path in {"/legal/privacy", "/legal/terms", "/support"}
        core_assets = "/api/omnia/core-assets/"
        upstream_path = self.path
        if path.startswith(core_assets):
            upstream_path = "/_next/" + self.path[len(core_assets) :]
        personal_response = (
            user is not None or path in {"/api/max/session", preview_path}
        ) and not path.startswith(core_assets + "static/")
        managed = legal_page or any(
            path == prefix or path.startswith(prefix + "/") for prefix in ("/api/omnia", "/api/max")
        )
        if managed:
            if public and user is None and not (
                legal_page or path.startswith(core_assets) or path in _PUBLIC_ANONYMOUS
            ):
                return self._reply(401, b"MAX authentication required")
            target, port = str(config["core_host"]), 3000
            headers = managed_headers(
                self.headers.items(),
                actor_precondition=self.command == "POST" and path in {
                    "/api/omnia/actions", "/api/omnia/consents",
                },
            )
            if public:
                headers = {
                    key: value for key, value in headers.items()
                    if key.casefold() not in {"forwarded", "cookie"}
                    and not key.casefold().startswith("x-forwarded-")
                }
                if session_value:
                    # The trusted core uses one canonical cookie name. None of
                    # these credentials are forwarded to generated product code.
                    headers["Cookie"] = f"{_SESSION_COOKIE}={session_value}"
            if legal_page:
                headers = {
                    k: v
                    for k, v in headers.items()
                    if k.casefold() not in {"accept-encoding", "rsc", "next-router-state-tree"}
                }
        else:
            if user is None:
                if not public and self._private_document(path, length) and route_port(
                    self.path, cast(list[dict[str, Any]], config["routes"]),
                ) is not None:
                    return self._owner_recovery()
                if (
                    public and self.command == "GET"
                    # Native MAX webviews need not send browser navigation headers.
                    # The launch root exposes only this trusted login shell, never
                    # product HTML or data. Embedded browser pages need it too.
                    and (path == "/" or (
                        "text/html" in self.headers.get("Accept", "")
                        and self.headers.get("Sec-Fetch-Dest", "") in {
                            "", "document", "iframe", "frame",
                        }
                    ))
                    and not path.startswith(("/api/", "/_next/", "/__omnia", "/auth"))
                ):
                    return self._bootstrap()
                return self._reply(401, b"MAX authentication required")
            if path == "/__omnia/identity":
                return self._reply(
                    200,
                    json.dumps(
                        {
                            "project_id": config["project_id"],
                            "user_id": user["id"],
                            "epoch": config["epoch"],
                        }
                    ).encode(),
                )
            routes = cast(list[dict[str, Any]], config["routes"])
            route_target_port = route_port(self.path, routes)
            if route_target_port is None:
                return self._reply(404)
            port = route_target_port
            target = str(config["machine_host"])
            headers = product_headers(
                dict(self.headers),
                project_id=str(config["project_id"]),
                epoch=int(config["epoch"]),
                user=user,
                origin=config.get("public_origin" if public else "preview_origin"),
            )
        connection = http.client.HTTPConnection(target, port, timeout=120)
        try:
            body = self.rfile.read(length) if length else None
            connection.request(self.command, upstream_path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(16 * 1024**2 + 1)
            if len(data) > 16 * 1024**2:
                return self._reply(502)
            if legal_page and "text/html" in (response.getheader("Content-Type") or ""):
                # The core and project have independent Next builds. Keep legal
                # assets in the managed namespace rather than loading project JS.
                data = data.replace(b"/_next/", core_assets.encode())
            self.send_response(response.status)
            for key, value in response_headers(
                response.getheaders(), managed=managed, personal=personal_response,
            ):
                self.send_header(key, value)
            if public and path == "/api/max/session" and self.command == "POST" and (
                response.status == 200
            ):
                for key, value in response.getheaders():
                    if key.casefold() == "set-cookie":
                        for cookie in embedded_session_cookies(value, config["secret"]):
                            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (OSError, http.client.HTTPException):
            self._reply(502)
        finally:
            connection.close()


if __name__ == "__main__":
    config_path = "/run/omnia-boundary/config.json"
    while not os.path.isfile(config_path):
        time.sleep(0.1)
    with open(config_path, encoding="utf-8") as config_file:
        configuration = cast(dict[str, Any], json.load(config_file))
    with BoundaryServer(("0.0.0.0", 3000), BoundaryHandler) as server:
        server.config = configuration
        server.serve_forever()
