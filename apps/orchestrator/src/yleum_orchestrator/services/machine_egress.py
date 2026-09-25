"""Public-only proxy and namespace policy; runnable with Python's standard library.

The project namespace firewall permits this proxy, not arbitrary DNS or Internet.
The proxy pins every connection to a checked DNS result. CONNECT is intentionally
protocol-neutral: package tools can use arbitrary public ports, never private IPs.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import select
import socket
import socketserver
import time
from dataclasses import asdict, dataclass
from typing import cast
from urllib.parse import urlsplit


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


class _Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 32
    denied_cidrs: list[str]


class _Handler(socketserver.BaseRequestHandler):
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
            if request_data:
                remote.sendall(request_data)
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
                    (remote if source is self.request else self.request).sendall(data)
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
    args = parser.parse_args()
    for cidr in args.deny:
        ipaddress.ip_network(cidr)
    with _Proxy((args.bind, 3128), _Handler) as server:
        server.denied_cidrs = args.deny
        server.serve_forever()


if __name__ == "__main__":
    main()
