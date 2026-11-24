"""The bootstrap proxy fetches only public npm tarballs and leaves SRI to pnpm."""
import hashlib
import io
import os
import socket
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from yleum_orchestrator.services import machine_egress as egress

RELAY = "/__yleum/npm/"
TARBALL = "react-dom/-/react-dom-18.3.1.tgz"
DATA = b"t" * (64 * 1024 + 7)


class Client:
    def __init__(self, data):
        self.source = io.BytesIO(data)
        self.output = io.BytesIO()

    def recv(self, size):
        return self.source.read(size)

    def sendall(self, data):
        self.output.write(data)

    def settimeout(self, timeout):
        pass

    def getsockname(self):
        return ("10.10.10.2", 3128)


class Response(io.BytesIO):
    def __init__(self, data=DATA, *, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(data))}
        self.read_sizes = []

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        self.read_sizes.append(size)
        return super().read1(size)


def request(monkeypatch, *, target=RELAY + TARBALL, method="GET", headers="", body=b"",
            response=None, relay_only=False, failure=None, cache=None):
    calls = []
    response = Response() if response is None else response

    @contextmanager
    def open_https(host, port, denied_cidrs, timeout):
        calls.append((host, port, denied_cidrs, timeout))
        if failure:
            raise failure
        yield SimpleNamespace(
            sock=Mock(), request=lambda *a, **kw: calls.append((a, kw)),
            getresponse=lambda: response,
        )

    monkeypatch.setattr(egress, "pinned_https", open_https)
    client = Client(f"{method} {target} HTTP/1.1\r\nHost: local\r\n{headers}\r\n".encode() + body)
    server = SimpleNamespace(
        relay_only=relay_only, denied_cidrs=["10.0.0.0/8"], npm_tarball_cache=cache,
    )
    egress._Handler(client, ("test", 0), server)
    return client.output.getvalue(), calls


@pytest.mark.parametrize(("encoded", "canonical"), [
    (TARBALL, TARBALL),
    ("@next/swc-linux-x64-gnu/-/swc-linux-x64-gnu-15.5.24.tgz",
     "@next/swc-linux-x64-gnu/-/swc-linux-x64-gnu-15.5.24.tgz"),
    ("@fontsource-variable%2fonest/-/onest-5.3.1.tgz",
     "@fontsource-variable/onest/-/onest-5.3.1.tgz"),
    ("%40types%2Fnode/-/node-22.2.0-rc.1%2Bbuild.2.tgz",
     "@types/node/-/node-22.2.0-rc.1+build.2.tgz"),
    ("%40s/3D/-/3D-1.0.0.tgz", "@s/3D/-/3D-1.0.0.tgz"),
])
def test_only_canonical_tarball_routes(encoded, canonical):
    assert egress.npm_tarball_path(RELAY + encoded) == "/" + canonical


@pytest.mark.parametrize("invalid", [
    "", "react", "react/-/other-1.0.0.tgz", TARBALL + "?token=private",
    TARBALL + "#part", TARBALL + "/extra", "//other.test/" + TARBALL,
    "../" + TARBALL, "scope%2f.." + TARBALL, "%2F" + TARBALL,
    "@scope%252F" + TARBALL, "react/-/react-1.0.0%0d%0a.tgz",
    "pkg/-/pkg-1.0.0.tgz" + "a" * 1050,
])
def test_invalid_archive_target_never_fetches(monkeypatch, invalid):
    wire, calls = request(monkeypatch, target=RELAY + invalid)
    assert wire.startswith(b"HTTP/1.1 400 ")
    assert not calls


@pytest.mark.parametrize("target", [RELAY + TARBALL, "http://10.10.10.2:3128" + RELAY + TARBALL])
def test_get_streams_exact_bytes_to_eof_without_forwarding_headers(monkeypatch, target):
    response = Response()
    wire, calls = request(monkeypatch, target=target, response=response, headers=(
        "Authorization: private\r\nCookie: private\r\nProxy-Authorization: private\r\n"
    ))
    header, body = wire.split(b"\r\n\r\n")
    assert header.startswith(b"HTTP/1.1 200 ")
    assert f"Content-Length: {len(DATA)}".encode() in header
    assert body == DATA and response.read_sizes == [65536, 7]
    assert calls == [
        ("registry.npmjs.org", 443, ["10.0.0.0/8"], egress.NPM_IO_TIMEOUT),
        (("GET", "/" + TARBALL), {"headers": {"Connection": "close"}}),
    ]


@pytest.mark.parametrize(("options", "status"), [
    ({"relay_only": True}, 403),
    ({"method": "POST"}, 405),
    ({"method": "HEAD"}, 405),
    ({"body": b"unexpected"}, 400),
    ({"headers": "Content-Length: 1\r\n"}, 400),
    ({"headers": "Transfer-Encoding: chunked\r\n"}, 400),
    ({"headers": "Content-Length: 0\r\nContent-Length: 10\r\n"}, 400),
    ({"headers": " Bad: value\r\n"}, 400),
])
def test_closed_request_surface(monkeypatch, options, status):
    wire, calls = request(monkeypatch, **options)
    assert wire.startswith(f"HTTP/1.1 {status} ".encode()) and not calls


@pytest.mark.parametrize(("upstream", "expected"), [(301, 502), (404, 404), (429, 429), (503, 503)])
def test_upstream_errors_and_redirects_do_not_expose_upstream_body(monkeypatch, upstream, expected):
    response = Response(b"sensitive", status=upstream, headers={"Location": "https://other.test/"})
    wire, calls = request(monkeypatch, response=response)
    assert wire.startswith(f"HTTP/1.1 {expected} ".encode())
    assert wire.endswith(b"\r\n\r\n") and b"sensitive" not in wire and b"Location" not in wire
    assert len(calls) == 2 and not response.read_sizes


@pytest.mark.parametrize("headers", [
    {}, {"Content-Length": "0"}, {"Content-Length": "999999999"}, {"Content-Length": "11, 11"},
    {"Content-Length": "11", "Content-Encoding": "gzip"},
    {"Content-Length": "11", "Transfer-Encoding": "chunked"},
])
def test_invalid_upstream_framing_is_rejected_before_stream(monkeypatch, headers):
    wire, _ = request(monkeypatch, response=Response(headers=headers))
    assert wire.startswith(b"HTTP/1.1 502 ") and wire.endswith(b"\r\n\r\n")


def test_truncated_stream_never_appends_error_status(monkeypatch):
    wire, _ = request(monkeypatch, response=Response(b"short", headers={"Content-Length": "21"}))
    h, body = wire.split(b"\r\n\r\n")
    assert h.startswith(b"HTTP/1.1 200 ") and b"Content-Length: 21" in h
    assert body == b"short"


@pytest.mark.parametrize(("failure", "status"), [
    (egress.DestinationDenied(), 502), (OSError(), 502), (TimeoutError(), 504),
])
def test_unavailable_upstream_has_distinct_gateway_status(monkeypatch, failure, status):
    wire, _ = request(monkeypatch, failure=failure)
    assert wire.startswith(f"HTTP/1.1 {status} ".encode())


def test_fallback_keeps_validated_address_and_hostname(monkeypatch):
    answers = ["2606:4700:4700::1111", "1.1.1.1"]
    dns = Mock(return_value=[(None, None, None, "", (ip, 443)) for ip in answers])
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    raw, tls = Mock(), Mock()
    connect = Mock(side_effect=[TimeoutError(), raw])
    monkeypatch.setattr(socket, "create_connection", connect)
    ctx = Mock()
    ctx.wrap_socket.return_value = tls
    monkeypatch.setattr(egress.ssl, "create_default_context", lambda: ctx)
    with egress.pinned_https(egress.NPM_HOST, 443, [], 10) as conn:
        assert conn.sock is tls
    assert [args[0] for args, _ in connect.call_args_list] == [(ip, 443) for ip in answers]
    assert connect.call_args_list[0].kwargs["timeout"] == 5
    dns.assert_called_once_with("registry.npmjs.org", 443, type=socket.SOCK_STREAM)
    ctx.wrap_socket.assert_called_once_with(raw, server_hostname="registry.npmjs.org")
    raw.close.assert_called_once()


def test_denied_cidr_in_mixed_result_never_connects(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (None, None, None, "", (ip, 443)) for ip in ("1.1.1.1", "8.8.8.8")
    ])
    connect = Mock()
    monkeypatch.setattr(socket, "create_connection", connect)
    with pytest.raises(egress.DestinationDenied), egress.pinned_https(
        egress.NPM_HOST, 443, ["8.8.8.0/24"], 10
    ):
        pytest.fail("denied answer accepted")
    connect.assert_not_called()


def test_total_deadline_stops_a_peer_sending_short_chunks_without_idle_timeout(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(egress.time, "monotonic", lambda: now[0])

    class SlowReader(Response):
        def read1(self, size):
            now[0] += egress.NPM_MAX_SECONDS / 2 + 1
            return super().read1(1)

    response = SlowReader(b"abcd")
    wire, _ = request(monkeypatch, response=response)
    header, body = wire.split(b"\r\n\r\n")
    assert b"Content-Length: 4" in header and body == b"a"


def test_cached_scoped_archive_precedes_network_and_uses_canonical_key(monkeypatch):
    cache = Mock(return_value=(io.BytesIO(DATA), len(DATA)))
    monkeypatch.setattr(egress, "cached_npm_tarball", cache)
    target = RELAY + "%40fontsource-variable%2Fonest/-/onest-5.3.1.tgz"
    wire, calls = request(monkeypatch, target=target, cache="/cache")
    assert wire.split(b"\r\n\r\n")[1] == DATA and not calls
    cache.assert_called_once_with("/cache", "/@fontsource-variable/onest/-/onest-5.3.1.tgz")
    wire, _ = request(monkeypatch, target=target, cache="/cache", relay_only=True)
    assert wire.startswith(b"HTTP/1.1 403 ") and cache.call_count == 1


def test_only_missing_entry_uses_registry(monkeypatch):
    cache = Mock(return_value=None)
    monkeypatch.setattr(egress, "cached_npm_tarball", cache)
    wire, calls = request(monkeypatch, cache="/cache")
    assert wire.split(b"\r\n\r\n")[1] == DATA and len(calls) == 2
    cache.side_effect = OSError()
    wire, calls = request(monkeypatch, cache="/cache")
    assert wire.startswith(b"HTTP/1.1 502 ") and not calls


@pytest.mark.skipif(os.name != "posix", reason="Linux project proxy uses openat")
def test_cache_only_opens_regular_bounded_files_under_an_unlinked_path(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    path = "/" + TARBALL
    filename = hashlib.sha256(path.encode()).hexdigest() + ".tgz"
    archive = cache / filename
    assert egress.cached_npm_tarball(str(cache), path) is None
    archive.write_bytes(b"archive")
    result = egress.cached_npm_tarball(str(cache), path)
    assert result is not None
    f, size = result
    with f:
        assert size == 7 and f.read() == b"archive"
    monkeypatch.setattr(egress, "NPM_MAX_BYTES", 4)
    with pytest.raises(OSError):
        egress.cached_npm_tarball(str(cache), path)
    archive.unlink()
    data = tmp_path / "external"
    data.write_bytes(b"key")
    archive.symlink_to(data)
    with pytest.raises(OSError):
        egress.cached_npm_tarball(str(cache), path)
    link = tmp_path / "alias"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        egress.cached_npm_tarball(str(link / "cache"), path)
    archive.unlink()
    os.mkfifo(archive)
    with pytest.raises(OSError):
        egress.cached_npm_tarball(str(cache), path)
