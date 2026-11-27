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
import os
import re
import select
import socket
import socketserver
import ssl
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, cast
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
NPM_RELAY_PREFIX = "/__yleum/npm/"
NPM_HOST = "registry.npmjs.org"
NPM_MAX_BYTES = 512 * 1024 * 1024
NPM_MAX_SECONDS = 300
NPM_IO_TIMEOUT = 20
NPM_METADATA_BUFFER = 1024 * 1024
# Package metadata can expand several-fold when parsed. If both slots are busy
# the caller still receives the original registry document via streaming.
_METADATA_SLOTS = threading.BoundedSemaphore(2)
_PACKAGE = r"[a-zA-Z0-9][a-zA-Z0-9._-]*"
_PACKAGE_PATH = re.compile(rf"(?:@{_PACKAGE}/)?{_PACKAGE}")
_TARBALL = re.compile(
    rf"(?:(?P<scope>@{_PACKAGE})/)?(?P<name>{_PACKAGE})/-/"
    r"(?P=name)-[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?\.tgz"
)


def npm_resource_path(target: str) -> tuple[str, bool]:
    """Return the fixed public registry path and whether it names a package document."""
    target = target.removeprefix(NPM_RELAY_PREFIX)
    if len(target) > 1024:
        raise DestinationDenied("invalid npm tarball")
    # npm clients also encode the scope delimiter. Decode just that delimiter,
    # leaving any other escape (including a second encoding layer) invalid.
    target = re.sub(r"^(?:@|%40)(" + _PACKAGE + r")(?:%2[fF]|/)", r"@\1/", target)
    target = re.sub(r"%2[bB]", "+", target)
    metadata = _PACKAGE_PATH.fullmatch(target) is not None
    if not metadata and not _TARBALL.fullmatch(target):
        raise DestinationDenied("invalid npm tarball")
    return "/" + target, metadata


def npm_metadata_body(data: bytes, package: str, registry: str) -> bytes:
    """Rewrite only standard, integrity-protected archives pnpm omits from the lockfile."""
    value = json.loads(data)
    if (not isinstance(value, dict) or value.get("name") != package
            or not isinstance(value.get("versions"), dict)):
        raise ValueError("invalid npm registry metadata")
    base = package.rsplit("/", 1)[-1]
    for version, item in value["versions"].items():
        if (not isinstance(item, dict) or item.get("name") != package
                or item.get("version") != version):
            continue
        dist = item.get("dist")
        if not isinstance(dist, dict) or not (dist.get("integrity") or dist.get("shasum")):
            continue
        archive = f"{package}/-/{base}-{version}.tgz"
        if _TARBALL.fullmatch(archive) and dist.get("tarball") == f"https://{NPM_HOST}/{archive}":
            dist["tarball"] = registry + archive
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()


def npm_io_budget(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("npm archive deadline")
    return min(remaining, NPM_IO_TIMEOUT)


def cached_npm_tarball(directory: str, path: str) -> tuple[BinaryIO, int] | None:
    """Read a trusted, flat mount. Never follow links or open a blocking device/FIFO."""
    parts = PurePosixPath(directory).parts
    if os.name != "posix" or not parts or parts[0] != "/" or ".." in parts:
        raise OSError("invalid npm cache mount")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    directory_fd = os.open("/", flags | os.O_DIRECTORY)
    try:
        for part in parts[1:]:
            following = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = following
        name = hashlib.sha256(path.encode("utf-8")).hexdigest() + ".tgz"
        fd = os.open(name, flags | os.O_NONBLOCK, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    finally:
        os.close(directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= NPM_MAX_BYTES:
            raise OSError("invalid npm cache entry")
        return os.fdopen(fd, "rb"), info.st_size
    except BaseException:
        os.close(fd)
        raise


def business_relay_origin(origin: str) -> tuple[str, int]:
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise DestinationDenied("invalid relay origin")
    return parse_authority(parsed.netloc, 443)


@contextmanager
def pinned_https(
    host: str, port: int, denied_cidrs: list[str], timeout: int,
) -> Iterator[http.client.HTTPSConnection]:
    addresses = checked_addresses(
        [str(answer[4][0]) for answer in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)],
        denied_cidrs,
    )
    connection = http.client.HTTPSConnection(host, port, timeout=timeout)
    deadline = time.monotonic() + timeout
    connect_timeout: float = timeout
    for index, address in enumerate(addresses):
        try:
            # Reserve part of the shared budget if the first network family is unreachable.
            share = connect_timeout / (len(addresses) - index)
            raw = socket.create_connection((address, port), timeout=share)
            break
        except OSError:
            connect_timeout = deadline - time.monotonic()
            if index == len(addresses) - 1 or connect_timeout <= 0:
                raise
    else:
        raise OSError("no public upstream address")
    try:
        if len(addresses) > 1:
            tls_timeout = deadline - time.monotonic()
            if tls_timeout <= 0:
                raise TimeoutError("upstream connection deadline")
            raw.settimeout(tls_timeout)
        connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        yield connection
    finally:
        connection.close()
        raw.close()


def deliver_business_receipt(origin: str, project: str, body: bytes, assertion: str) -> int:
    """Send the signed receipt only to its configured and TLS-verified platform origin."""
    host, port = business_relay_origin(origin)
    path = f"/api/runtime/projects/{UUID(project)}/analytics"
    with pinned_https(host, port, [], 2) as connection:
        connection.request("POST", path, body, {
            "Content-Type": "application/json", "X-Yleum-Business-Assertion": assertion,
            "Connection": "close",
        })
        # No response body or redirect is proxied into the product namespace.
        status = connection.getresponse().status
        return status if status in {200, 201, 202, 204, 400, 401, 403, 409, 429} else 503


class _Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 32
    denied_cidrs: list[str]
    analytics_project: str = ""
    analytics_origin: str = ""
    relay_only: bool = False
    npm_tarball_cache: str | None = None


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
    @staticmethod
    def relay_headers(lines: list[str]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in lines:
            key, separator, value = line.partition(":")
            key = key.lower()
            if (not separator or not re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]+", key)
                    or key in headers):
                raise DestinationDenied("invalid relay headers")
            headers[key] = value.strip()
        return headers

    def npm_status(self, status: str) -> None:
        self.request.sendall(
            f"HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode()
        )

    def npm_upstream_status(self, status: int) -> None:
        self.npm_status({
            404: "404 Not Found", 429: "429 Too Many Requests",
            503: "503 Service Unavailable",
        }.get(status, "502 Bad Gateway"))

    def send_npm_metadata(
        self, response: http.client.HTTPResponse, upstream: socket.socket, deadline: float,
        path: str, buffer: bool,
    ) -> None:
        length = response.getheader("Content-Length")
        transfer = response.getheader("Transfer-Encoding")
        if (response.getheader("Content-Encoding", "identity") != "identity"
                or transfer not in (None, "chunked") or (transfer and length)
                or (length is not None and (not re.fullmatch(r"[0-9]{1,9}", length)
                                             or int(length) > NPM_MAX_BYTES))):
            raise OSError("unexpected npm metadata framing")
        declared = int(length) if length is not None else None

        def chunks() -> Iterator[bytes]:
            total = 0
            while True:
                upstream.settimeout(npm_io_budget(deadline))
                chunk = response.read1(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > NPM_MAX_BYTES:
                    raise OSError("oversize npm metadata")
                yield chunk
            if declared is not None and total != declared:
                raise OSError("incomplete npm metadata")

        stream = chunks()
        prefix = bytearray()
        if buffer and (declared is None or declared <= NPM_METADATA_BUFFER):
            for chunk in stream:
                prefix.extend(chunk)
                if len(prefix) > NPM_METADATA_BUFFER:
                    break
            else:
                ip, port = self.request.getsockname()[:2]
                registry = f"http://{ip}:{port}{NPM_RELAY_PREFIX}"
                data = npm_metadata_body(bytes(prefix), path[1:], registry)
                self.request.settimeout(npm_io_budget(deadline))
                headers = f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                try:
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                         + headers + data)
                except OSError:
                    # The response may already have reached the client.
                    return
                return
        # Large documents preserve the original upstream archive URLs and never need
        # to fit in the sidecar's memory. An incomplete response has no final chunk.
        try:
            self.request.settimeout(npm_io_budget(deadline))
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                 b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
            if prefix:
                self.request.sendall(f"{len(prefix):x}\r\n".encode() + prefix + b"\r\n")
            for chunk in stream:
                self.request.settimeout(npm_io_budget(deadline))
                self.request.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.request.sendall(b"0\r\n\r\n")
        except (OSError, http.client.HTTPException):
            return

    def npm_metadata(self, path: str, accept: str, deadline: float) -> None:
        server = cast(_Proxy, self.server)
        media = ("application/vnd.npm.install-v1+json"
                 if "application/vnd.npm.install-v1+json" in accept else "application/json")
        acquired = _METADATA_SLOTS.acquire(blocking=False)
        try:
            for attempt in range(2):
                try:
                    with pinned_https(
                        NPM_HOST, 443, server.denied_cidrs, NPM_IO_TIMEOUT,
                    ) as connection:
                        upstream = connection.sock
                        if upstream is None:
                            raise OSError("npm connection unavailable")
                        upstream.settimeout(npm_io_budget(deadline))
                        connection.request(
                            "GET", path, headers={"Accept": media, "Connection": "close"},
                        )
                        with connection.getresponse() as response:
                            if response.status != 200:
                                self.npm_upstream_status(response.status)
                            else:
                                self.send_npm_metadata(response, upstream, deadline, path, acquired)
                            return
                except DestinationDenied:
                    raise
                except (OSError, http.client.HTTPException, ValueError, RecursionError):
                    if attempt:
                        raise
        finally:
            if acquired:
                _METADATA_SLOTS.release()

    def send_npm_archive(
        self, size: int, read: Callable[[int], bytes], deadline: float,
        upstream: socket.socket | None = None,
    ) -> None:
        self.request.settimeout(npm_io_budget(deadline))
        try:
            self.request.sendall(
                f"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
                f"Content-Length: {size}\r\nConnection: close\r\n\r\n".encode()
            )
            remaining = size
            while remaining:
                if upstream is not None:
                    upstream.settimeout(npm_io_budget(deadline))
                chunk = read(min(65536, remaining))
                if not chunk:
                    return
                self.request.settimeout(npm_io_budget(deadline))
                self.request.sendall(chunk)
                remaining -= len(chunk)
        except (OSError, http.client.HTTPException):
            # Never append an error page to an incomplete 200 response.
            # Closing early exposes the actual length mismatch to the installer.
            return

    def npm_resource(self, method: str, target: str, lines: list[str], body: bytes) -> None:
        if method != "GET":
            self.npm_status("405 Method Not Allowed")
            return
        try:
            path, metadata = npm_resource_path(target)
            # This capability never forwards a client's headers, cookies, auth or body.
            headers = self.relay_headers(lines)
            credential_headers = {"authorization", "proxy-authorization", "cookie"}
            if metadata and credential_headers.intersection(headers):
                self.npm_status("403 Forbidden")
                return
            if (body or "transfer-encoding" in headers or "expect" in headers
                    or headers.get("content-length", "0") != "0"):
                raise DestinationDenied("invalid npm request")
        except DestinationDenied:
            self.npm_status("400 Bad Request")
            return

        deadline = time.monotonic() + NPM_MAX_SECONDS
        try:
            server = cast(_Proxy, self.server)
            if metadata:
                self.npm_metadata(path, headers.get("accept", ""), deadline)
                return
            if server.npm_tarball_cache:
                cached = cached_npm_tarball(server.npm_tarball_cache, path)
                if cached is not None:
                    archive, size = cached
                    with archive:
                        self.send_npm_archive(size, archive.read, deadline)
                    return
            with pinned_https(NPM_HOST, 443, server.denied_cidrs, NPM_IO_TIMEOUT) as connection:
                # Capture the socket before getresponse() detaches it for Connection: close.
                upstream = connection.sock
                if upstream is None:
                    raise OSError("npm connection unavailable")
                upstream.settimeout(npm_io_budget(deadline))
                connection.request("GET", path, headers={"Connection": "close"})
                with connection.getresponse() as response:
                    if response.status != 200:
                        self.npm_upstream_status(response.status)
                        return
                    length = response.getheader("Content-Length", "")
                    if (not re.fullmatch(r"[0-9]{1,9}", length)
                            or not 0 < int(length) <= NPM_MAX_BYTES
                            or response.getheader("Transfer-Encoding") is not None
                            or response.getheader("Content-Encoding", "identity") != "identity"):
                        raise OSError("unexpected npm archive framing")

                    self.send_npm_archive(int(length), response.read1, deadline, upstream)
        except (OSError, http.client.HTTPException, ValueError, RecursionError) as exc:
            self.npm_status("504 Gateway Timeout" if isinstance(exc, TimeoutError)
                            else "502 Bad Gateway")

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
        headers = self.relay_headers(lines[1:])
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
            # pnpm may send an absolute URL to its configured HTTP proxy, even
            # when the registry endpoint and that proxy share this listener.
            if target.startswith("http://") and NPM_RELAY_PREFIX in target:
                ip, port = self.request.getsockname()[:2]
                local_origin = f"http://{ip}:{port}"
                if target.startswith(local_origin + NPM_RELAY_PREFIX):
                    target = target[len(local_origin):]
            if target.startswith(NPM_RELAY_PREFIX):
                self.npm_resource(method, target, lines[1:], body)
                return
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
    parser.add_argument("--npm-tarball-cache")
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
        server.npm_tarball_cache = args.npm_tarball_cache
        server.serve_forever()


if __name__ == "__main__":
    main()
