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
        managed = any(
            path == prefix or path.startswith(prefix + "/") for prefix in ("/api/omnia", "/api/max")
        )
        if managed:
            target, port = str(config["core_host"]), 3000
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.casefold() not in _HOP and not key.casefold().startswith("x-omnia-")
            }
        else:
            cookies = http.cookies.SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
                session = cookies.get("__Host-max_session")
                user = verified_user(session.value if session else "", config["secret"])
            except http.cookies.CookieError:
                user = None
            if user is None:
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
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(16 * 1024**2 + 1)
            if len(data) > 16 * 1024**2:
                return self._reply(502)
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
