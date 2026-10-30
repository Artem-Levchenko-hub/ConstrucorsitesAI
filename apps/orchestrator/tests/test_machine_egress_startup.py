"""Regress the observed packetization-sensitive startup without changing stream bytes."""
from collections import deque
from types import SimpleNamespace

import pytest

from yleum_orchestrator.services import machine_egress as egress

HELLO = b"\x16\x03\x01\x06\x45\x01\x00\x06\x41" + b"h" * 1601
PAYLOAD = HELLO + bytes(range(256)) * 128
REPLY = b"\x17\x03\x03\x00\x07reply\x00\xff"


class Clock:
    def __init__(self):
        self.now = 100.0
        self.pauses = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.pauses.append(duration)
        self.now += duration


class Peer:
    """External path model: a large unshaped startup is reset; later bulk is fine."""

    def __init__(self, sensitive=True):
        self.sensitive = sensitive
        self.received = bytearray()
        self.timeout = 20.0
        self.nodelay = 0
        self.closed = False
        self.fail_after = None
        self.timeouts = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout
        self.timeouts.append(timeout)

    def getsockopt(self, _level, _option):
        return self.nodelay

    def setsockopt(self, _level, _option, value):
        self.nodelay = value

    def sendall(self, data):
        if self.fail_after is not None and len(self.received) >= self.fail_after:
            raise TimeoutError("test path stopped accepting bytes")
        if self.sensitive and len(self.received) < len(HELLO) and len(data) > 512:
            raise ConnectionResetError("unshaped startup")
        self.received.extend(data)

    def recv(self, _size):
        return REPLY

    def close(self):
        self.closed = True


class Client:
    def __init__(self, chunks):
        self.chunks = deque(chunks)
        self.received = bytearray()

    def settimeout(self, _timeout):
        pass

    def recv(self, _size):
        return self.chunks.popleft() if self.chunks else b""

    def sendall(self, data):
        self.received.extend(data)


def forward(monkeypatch, peer, chunks, expected_payload):
    client = Client(chunks)
    monkeypatch.setattr(egress.socket, "getaddrinfo", lambda *_a, **_k: [
        (egress.socket.AF_INET, egress.socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ])

    def connect(address, *, timeout):
        assert address == ("8.8.8.8", 443) and timeout == 20
        return peer

    monkeypatch.setattr(egress.socket, "create_connection", connect)

    def select(_readable, _write, _errors, timeout):
        assert timeout == 30
        return ([peer] if peer.received == expected_payload and not client.received.endswith(REPLY)
                else [client]), [], []

    monkeypatch.setattr(egress.select, "select", select)
    handler = object.__new__(egress._Handler)
    handler.request = client
    handler.server = SimpleNamespace(denied_cidrs=[], relay_only=False)
    handler.handle()
    return client


@pytest.mark.parametrize("leftover", [0, 700, 1610])
def test_connect_delivers_intact_stream_over_sensitive_startup(monkeypatch, leftover):
    """Fails if either CONNECT's leftover or a subsequent recv bypasses shaping."""
    peer = Peer()
    header = b"CONNECT 8.8.8.8:443 HTTP/1.1\r\nHost: 8.8.8.8:443\r\n\r\n"
    client = forward(monkeypatch, peer, [
        header + PAYLOAD[:leftover], PAYLOAD[leftover:leftover + 777],
        PAYLOAD[leftover + 777:], b"",
    ], PAYLOAD)
    assert peer.received == PAYLOAD
    assert client.received == b"HTTP/1.1 200 Connection Established\r\n\r\n" + REPLY
    assert peer.closed
    assert peer.timeout == 20.0 and peer.nodelay == 0


def test_tiny_client_reads_cannot_multiply_startup_delay(monkeypatch):
    clock, peer = Clock(), Peer(sensitive=False)
    monkeypatch.setattr(egress.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(egress.time, "sleep", clock.sleep)
    writer = egress._ConnectWriter(peer)
    for value in PAYLOAD[:2048]:
        writer.sendall(bytes([value]))
    pause_before_bulk = sum(clock.pauses)
    writer.sendall(PAYLOAD[2048:])
    assert peer.received == PAYLOAD
    assert 0 < pause_before_bulk <= 0.072001
    assert sum(clock.pauses) == pause_before_bulk
    assert peer.timeout == 20.0 and peer.nodelay == 0


def test_timeout_never_replays_a_partially_sent_prefix(monkeypatch):
    clock, peer = Clock(), Peer(sensitive=False)
    peer.fail_after = 1
    monkeypatch.setattr(egress.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(egress.time, "sleep", clock.sleep)
    writer = egress._ConnectWriter(peer)
    with pytest.raises(TimeoutError):
        writer.sendall(PAYLOAD)
    assert 0 < len(peer.received) < len(PAYLOAD)
    assert PAYLOAD.startswith(peer.received)
    assert peer.timeout == 20.0


def test_one_original_write_has_one_deadline_including_bulk_tail(monkeypatch):
    clock, peer = Clock(), Peer(sensitive=False)
    peer.timeout = 0.080
    original_send = peer.sendall

    def slow_send(data):
        duration = 0.002 if len(data) <= 512 else 0.010
        if peer.timeout < duration:
            clock.now += peer.timeout
            raise TimeoutError("write exceeded remaining deadline")
        clock.now += duration
        original_send(data)

    monkeypatch.setattr(egress.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(egress.time, "sleep", clock.sleep)
    monkeypatch.setattr(peer, "sendall", slow_send)
    writer = egress._ConnectWriter(peer)
    with pytest.raises(TimeoutError):
        writer.sendall(PAYLOAD)
    assert clock.now == pytest.approx(100.080)
    assert peer.received == PAYLOAD[:2048]
    assert peer.timeout == 0.080 and peer.nodelay == 0


def test_separate_read_idle_does_not_spend_next_write_deadline(monkeypatch):
    clock, peer = Clock(), Peer(sensitive=False)
    monkeypatch.setattr(egress.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(egress.time, "sleep", clock.sleep)
    writer = egress._ConnectWriter(peer)
    writer.sendall(PAYLOAD[:10])
    assert peer.timeout == 20.0
    clock.now += 25.0  # Inside the existing 30-second client-read idle allowance.
    writer.sendall(PAYLOAD[10:])
    assert peer.received == PAYLOAD
    assert peer.timeout == 20.0 and peer.nodelay == 0


def test_scheduler_oversleep_stops_further_intentional_delays(monkeypatch):
    clock, peer = Clock(), Peer(sensitive=False)

    def oversleep(duration):
        clock.pauses.append(duration)
        clock.now += duration + 0.2

    monkeypatch.setattr(egress.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(egress.time, "sleep", oversleep)
    egress._ConnectWriter(peer).sendall(PAYLOAD)
    assert peer.received == PAYLOAD
    assert len(clock.pauses) == 1


def test_opaque_non_tls_connect_data_is_preserved(monkeypatch):
    payload = b"SSH-2.0-test\r\n\x00\xff" * 300
    peer = Peer()
    client = forward(monkeypatch, peer, [
        b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n" + payload,
    ], payload)
    assert peer.received == payload
    assert client.received.endswith(REPLY)


def test_plain_http_keeps_header_rewrite_without_startup_delays(monkeypatch):
    peer, clock = Peer(sensitive=False), Clock()
    monkeypatch.setattr(egress.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(egress.time, "sleep", clock.sleep)
    client = forward(monkeypatch, peer, [
        b"POST http://8.8.8.8:443/path?q=one HTTP/1.1\r\n"
        b"Host: other\r\nProxy-Authorization: hidden\r\nContent-Length: 3\r\n\r\nabc",
    ], b"POST /path?q=one HTTP/1.1\r\nHost: 8.8.8.8:443\r\n"
       b"Content-Length: 3\r\nConnection: close\r\n\r\nabc")
    assert peer.received == (
        b"POST /path?q=one HTTP/1.1\r\nHost: 8.8.8.8:443\r\n"
        b"Content-Length: 3\r\nConnection: close\r\n\r\nabc"
    )
    assert client.received == REPLY
    assert clock.pauses == [] and peer.nodelay == 0
