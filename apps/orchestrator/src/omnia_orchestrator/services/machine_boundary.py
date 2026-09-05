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
from typing import Any, cast
from urllib.parse import unquote, urlsplit

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
_BOOTSTRAP_SCRIPT = """
(() => {
  const message = document.getElementById('status');
  const retry = document.getElementById('retry');
  let busy = false;
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
      const session = await fetch('/api/max/session', {
        method: 'POST', credentials: 'include', cache: 'no-store',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({initData: app.initData}), signal: AbortSignal.timeout(15000)
      });
      if (!session.ok) throw new Error('MAX authentication failed');
      const identity = await fetch('/__omnia/identity', {
        credentials: 'include', cache: 'no-store', signal: AbortSignal.timeout(10000)
      });
      if (!identity.ok) {
        message.textContent = 'MAX не сохранил вход. Закройте приложение и откройте снова.';
        return;
      }
      location.replace(location.href);
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


def product_headers(
    headers: dict[str, str], *, project_id: str, epoch: int, user: dict[str, Any]
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
    return clean


class BoundaryServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    config: dict[str, Any]


class BoundaryHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        # No signed bootstrap URL, cookie, body, or project credentials in logs.
        pass

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

    def _forward(self) -> None:
        config = cast(BoundaryServer, self.server).config
        path = canonical_request_path(self.path)
        if path is None or self.headers.get("Transfer-Encoding"):
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
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            session = cookies.get("__Host-max_session")
            user = verified_user(session.value if session else "", config["secret"])
        except http.cookies.CookieError:
            user = None
        # Public MAX identities are numeric. Never promote the owner preview user.
        if public and user is not None and not re.fullmatch(r"[0-9]+", user["id"]):
            user = None
        legal_page = path in {"/legal/privacy", "/legal/terms", "/support"}
        core_assets = "/api/omnia/core-assets/"
        upstream_path = self.path
        if path.startswith(core_assets):
            upstream_path = "/_next/" + self.path[len(core_assets) :]
        managed = legal_page or any(
            path == prefix or path.startswith(prefix + "/") for prefix in ("/api/omnia", "/api/max")
        )
        if managed:
            if public and user is None and not (
                legal_page or path.startswith(core_assets) or path in _PUBLIC_ANONYMOUS
            ):
                return self._reply(401, b"MAX authentication required")
            target, port = str(config["core_host"]), 3000
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.casefold() not in _HOP and not key.casefold().startswith("x-omnia-")
            }
            if public:
                headers = {
                    key: value for key, value in headers.items()
                    if key.casefold() != "forwarded"
                    and not key.casefold().startswith("x-forwarded-")
                }
            if legal_page:
                headers = {
                    k: v
                    for k, v in headers.items()
                    if k.casefold() not in {"accept-encoding", "rsc", "next-router-state-tree"}
                }
        else:
            if user is None:
                if (
                    public and self.command == "GET"
                    and "text/html" in self.headers.get("Accept", "")
                    and self.headers.get("Sec-Fetch-Dest", "document") == "document"
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
            for key, value in response.getheaders():
                if key.casefold() not in _HOP and (managed or key.casefold() != "set-cookie"):
                    self.send_header(key, value)
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
