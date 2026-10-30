"""Public-only proxy and namespace policy; runnable with Python's standard library.

The project namespace firewall permits this proxy, not arbitrary DNS or Internet.
The proxy pins every connection to a checked DNS result. CONNECT is intentionally
protocol-neutral: package tools can use arbitrary public ports, never private IPs.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import re
import select
import socket
import socketserver
import ssl
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID


class DestinationDenied(ValueError):
    pass


def public_destination(address: str, denied_cidrs: list[str] | tuple[str, ...] = ()) -> bool:
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        return False
    if not value.is_global or value.is_multicast or value.is_reserved:
        return False
    if value.version == 6 and (value.ipv4_mapped or value.sixtofour or value.teredo):
        return False
    return not any(value in ipaddress.ip_network(cidr) for cidr in denied_cidrs)


def checked_addresses(addresses: list[str], denied_cidrs: list[str]) -> list[str]:
    if not addresses or not all(public_destination(item, denied_cidrs) for item in addresses):
        raise DestinationDenied("destination is not exclusively public")
    return list(dict.fromkeys(addresses))


def parse_authority(authority: str, default_port: int) -> tuple[str, int]:
    if any(c in authority for c in "@/\\?#\r\n\x00 \t"):
        raise DestinationDenied("invalid authority")
    try:
        parsed = urlsplit("//" + authority)
        host, port = parsed.hostname, parsed.port or default_port
        if not host or parsed.port == 0 or not 1 <= port <= 65535:
            raise ValueError("invalid port")
        try:
            numeric = ipaddress.ip_address(host)
        except ValueError:
            numeric = None
        if numeric is not None and not public_destination(host):
            raise DestinationDenied("private destination")
        return host, port
    except ValueError as exc:
        raise DestinationDenied("invalid destination") from exc


@dataclass(frozen=True)
class EgressReadiness:
    ready: bool
    policy_digest: str
    reason: str


@dataclass(frozen=True)
class GuardPolicy:
    workspace_id: str
    proxy_ip: str
    data_endpoints: tuple[tuple[str, int], ...] = ()

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def allows(self, address: str, port: int) -> bool:
        return (address, port) == (self.proxy_ip, 3128) or (address, port) in self.data_endpoints


BUSINESS_RELAY_PATH = "/__yleum/business-analytics"


def business_relay_origin(origin: str) -> tuple[str, int]:
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise DestinationDenied("invalid relay origin")
    return parse_authority(parsed.netloc, 443)


def deliver_business_receipt(origin: str, project: str, body: bytes, assertion: str) -> int:
    """Fixed destination; the API verifies the purpose key and durable receipt ID.

    Unlike general proxy requests this explicit capability may reach the public
    platform address. It cannot select another host/path/method or follow redirects.
    DNS is pinned before TLS, and the original hostname is verified by TLS.
    """
    host, port = business_relay_origin(origin)
    path = f"/api/runtime/projects/{UUID(project)}/analytics"
    addresses = checked_addresses(
        [str(answer[4][0]) for answer in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)],
        [],
    )
    connection = http.client.HTTPSConnection(host, port, timeout=2)
    raw = socket.create_connection((addresses[0], port), timeout=2)
    try:
        connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        connection.request("POST", path, body, {
            "Content-Type": "application/json", "X-Yleum-Business-Assertion": assertion,
            "Connection": "close",
        })
        # No response body or redirect is proxied into the product namespace.
        status = connection.getresponse().status
        return status if status in {200, 201, 202, 204, 400, 401, 403, 409, 429} else 503
    finally:
        connection.close()
        raw.close()


class _Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 32
    denied_cidrs: list[str]
    analytics_project: str = ""
    analytics_origin: str = ""
    relay_only: bool = False


class _ConnectWriter:
    """Pace a small opaque prefix on the observed packetization-sensitive path.

    No TLS inspection or crypto policy changes. Budgets span all client reads,
    while each original write retains a single timeout, including its bulk tail.
    """

    def __init__(self, remote: socket.socket) -> None:
        self.remote = remote
        self.remaining = 2048
        self.pause_budget = 0.072
        self.last_write: float | None = None
        self.nodelay = remote.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
        self.changed_nodelay = False

    def _finish_prefix(self) -> None:
        if self.changed_nodelay:
            self.remote.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, self.nodelay)
            self.changed_nodelay = False

    def _send_before(self, data: bytes, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("CONNECT startup write deadline elapsed")
        self.remote.settimeout(remaining)
        self.remote.sendall(data)

    def _pace(self, deadline: float) -> None:
        now = time.monotonic()
        if self.last_write is None or self.pause_budget <= 0:
            return
        delay = min(0.012 - (now - self.last_write), self.pause_budget, deadline - now)
        if delay > 0:
            time.sleep(delay)
            self.pause_budget = max(
                0.0, self.pause_budget - max(delay, time.monotonic() - now),
            )

    def sendall(self, data: bytes) -> None:
        if not data:
            return
        if not self.remaining:
            self.remote.sendall(data)
            return
        original_timeout = self.remote.gettimeout()
        deadline = time.monotonic() + (
            original_timeout if original_timeout is not None else 20.0
        )
        try:
            if not self.changed_nodelay:
                self.remote.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.changed_nodelay = True
            offset = 0
            while offset < len(data) and self.remaining:
                length = min(384, self.remaining, len(data) - offset)
                self._pace(deadline)
                self._send_before(data[offset:offset + length], deadline)
                self.last_write = time.monotonic()
                self.remaining -= length
                offset += length
            if not self.remaining:
                self._finish_prefix()
            if offset < len(data):
                self._send_before(data[offset:], deadline)
        except BaseException:
            self._finish_prefix()
            raise
        finally:
            self.remote.settimeout(original_timeout)


class _Handler(socketserver.BaseRequestHandler):
    def relay(self, remote: socket.socket, send_upstream: Callable[[bytes], None]) -> None:
        count = 0
        started = time.monotonic()
        while time.monotonic() - started < 900 and count < 512 * 1024 * 1024:
            readable, _, _ = select.select([self.request, remote], [], [], 30)
            if not readable:
                break
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                count += len(data)
                if source is self.request:
                    send_upstream(data)
                else:
                    self.request.sendall(data)

    def business_receipt(self, lines: list[str], body: bytes) -> None:
        server = cast(_Proxy, self.server)
        headers: dict[str, str] = {}
        for line in lines[1:]:
            key, separator, value = line.partition(":")
            key = key.lower()
            if not separator or key in headers:
                raise DestinationDenied("invalid relay headers")
            headers[key] = value.strip()
        length = headers.get("content-length", "")
        assertion = headers.get("x-yleum-business-assertion", "")
        if (not server.analytics_project or not server.analytics_origin
                or "transfer-encoding" in headers or not re.fullmatch(r"[0-9]{1,4}", length)
                or not 1 <= int(length) <= 4096 or not 1 <= len(assertion) <= 4096):
            raise DestinationDenied("invalid relay request")
        self.request.settimeout(2)
        while len(body) < int(length):
            chunk = self.request.recv(int(length) - len(body))
            if not chunk:
                raise DestinationDenied("incomplete receipt")
            body += chunk
        if len(body) != int(length):
            raise DestinationDenied("invalid receipt length")
        value = json.loads(body)
        if (not isinstance(value, dict) or set(value) != {"event_id", "kind", "event_type"}
                or value["kind"] != "action" or not isinstance(value["event_type"], str)
                or value["event_type"] not in {
                    "order_created", "lead_created", "booking_created"}
                or not isinstance(value["event_id"], str)):
            raise DestinationDenied("invalid receipt")
        UUID(value["event_id"])
        try:
            status = deliver_business_receipt(
                server.analytics_origin, server.analytics_project, body, assertion,
            )
        except (OSError, http.client.HTTPException):
            status = 503
        self.request.sendall(
            f"HTTP/1.1 {status} Receipt\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode()
        )

    def handle(self) -> None:
        remote = None
        try:
            self.request.settimeout(30)
            header = bytearray()
            while b"\r\n\r\n" not in header:
                part = self.request.recv(4096)
                if not part or len(header) + len(part) > 65536:
                    raise DestinationDenied("invalid header")
                header.extend(part)
            raw_header, body = bytes(header).split(b"\r\n\r\n", 1)
            lines = raw_header.decode("iso-8859-1").split("\r\n")
            method, target, version = lines[0].split(" ")
            if version not in ("HTTP/1.0", "HTTP/1.1"):
                raise DestinationDenied("invalid protocol")
            if method == "POST" and target == BUSINESS_RELAY_PATH:
                self.business_receipt(lines, body)
                return
            if cast(_Proxy, self.server).relay_only:
                raise DestinationDenied("only business receipts allowed")
            if method == "CONNECT":
                host, port = parse_authority(target, 443)
                request_data = body
            else:
                parsed = urlsplit(target)
                if parsed.scheme != "http":
                    raise DestinationDenied("use CONNECT for non-HTTP traffic")
                host, port = parse_authority(parsed.netloc, 80)
                # One HTTP request per connection; no host switch through pipelining.
                kept = []
                for line in lines[1:]:
                    key, separator, _value = line.partition(":")
                    if not separator:
                        raise DestinationDenied("invalid header")
                    if key.casefold() not in {
                        "proxy-authorization",
                        "proxy-connection",
                        "connection",
                        "host",
                    }:
                        kept.append(line)
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                request_data = (
                    f"{method} {path} HTTP/1.1\r\nHost: {parsed.netloc}\r\n"
                    + "\r\n".join(kept)
                    + "\r\nConnection: close\r\n\r\n"
                ).encode("iso-8859-1") + body
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            server = cast(_Proxy, self.server)
            addresses = checked_addresses(
                [str(item[4][0]) for item in answers],
                server.denied_cidrs,
            )
            last_error = None
            for address in addresses:
                try:
                    # Numeric address: never resolve the hostname again after validation.
                    remote = socket.create_connection((address, port), timeout=20)
                    break
                except OSError as exc:
                    last_error = exc
            if remote is None:
                raise OSError("public destination unavailable") from last_error
            if method == "CONNECT":
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            send_upstream = (
                _ConnectWriter(remote).sendall if method == "CONNECT" else remote.sendall
            )
            if request_data:
                send_upstream(request_data)
            self.relay(remote, send_upstream)
        except (OSError, ValueError):
            try:
                self.request.sendall(
                    b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n"
                    b"Content-Length: 24\r\n\r\nDestination unavailable\n"
                )
            except OSError:
                pass
        finally:
            if remote:
                remote.close()
            # Never log URL, query, headers, auth, or installer output.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--deny", action="append", default=[])
    parser.add_argument("--analytics-project", default="")
    parser.add_argument("--analytics-origin", default="")
    parser.add_argument("--relay-only", action="store_true")
    args = parser.parse_args()
    if args.analytics_project or args.analytics_origin or args.relay_only:
        UUID(args.analytics_project)
        business_relay_origin(args.analytics_origin)
    for cidr in args.deny:
        ipaddress.ip_network(cidr)
    with _Proxy((args.bind, 3128), _Handler) as server:
        server.denied_cidrs = args.deny
        server.analytics_project = args.analytics_project
        server.analytics_origin = args.analytics_origin
        server.relay_only = args.relay_only
        server.serve_forever()


if __name__ == "__main__":
    main()
