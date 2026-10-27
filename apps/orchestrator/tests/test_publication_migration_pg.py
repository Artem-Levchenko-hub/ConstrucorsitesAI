"""Transport closes on lost framing; identities never collapse to pod names."""

import importlib
import ssl
from copy import deepcopy
from types import SimpleNamespace

import pytest


def implementation():
    name = "yleum_orchestrator.services.publication_migration_pg"
    assert importlib.util.find_spec(name), "bounded migration PG adapter missing"
    return importlib.import_module(name)


def real_parser_transport(monkeypatch, chunks, *, recv_cost=0, send_cost=0, send_size=None):
    """Real WebSocket/WSClient parsers over a threadless, finite fake TLS socket."""
    import kubernetes
    import websocket
    from kubernetes.stream import ws_client

    m = implementation()
    clock = SimpleNamespace(now=100.0)

    class TLS:
        _closed = False
        timeout = 5

        def __init__(self):
            self.chunks = list(chunks)
            self.received_timeouts = []
            self.sent_timeouts = []
            self.sent = []

        def gettimeout(self):
            return self.timeout

        def settimeout(self, timeout):
            self.timeout = timeout

        def fileno(self):
            return 123

        def pending(self):
            return sum(len(chunk) for chunk in self.chunks if isinstance(chunk, bytes))

        def spend(self, cost):
            assert self.timeout is not None and self.timeout > 0
            if cost > self.timeout:
                clock.now += self.timeout
                raise TimeoutError("synthetic timeout")
            clock.now += cost

        def recv(self, size):
            self.received_timeouts.append(self.timeout)
            if not self.chunks or self.chunks[0] is None:
                if self.chunks:
                    self.chunks.pop(0)
                self.spend(self.timeout + 1)
            self.spend(recv_cost)
            chunk = self.chunks.pop(0)
            result, remainder = chunk[:size], chunk[size:]
            if remainder:
                self.chunks.insert(0, remainder)
            return result

        def send(self, data):
            self.sent_timeouts.append(self.timeout)
            self.spend(send_cost)
            count = len(data) if send_size is None else min(send_size, len(data))
            self.sent.append(bytes(data[:count]))
            return count

        def close(self):
            self._closed = True

    tls = TLS()
    real_ws = websocket.WebSocket(enable_multithread=False)
    real_ws.sock = tls
    real_ws.connected = True
    real_ws.set_mask_key(lambda _: b"abcd")
    monkeypatch.setattr(websocket, "create_connection", lambda *args, **kwargs: real_ws)
    # Reproduce OS-not-readable while TLS already owns decrypted bytes.
    monkeypatch.setattr(ws_client, "select", SimpleNamespace(select=lambda *args: ([], [], [])))
    monkeypatch.setattr(m.time, "monotonic", lambda: clock.now)
    config = kubernetes.client.Configuration()
    config.host = "https://127.0.0.1"
    stream = m._websocket_call(config, "GET", config.host + "/exec",
                             binary=True, _preload_content=False, headers={})
    return stream, real_ws, tls, clock


def server_frame(payload, *, opcode=2, fin=1):
    from websocket import ABNF

    return ABNF(fin=fin, opcode=opcode, mask_value=0, data=payload).format()


def test_real_parser_reads_tls_buffer_despite_empty_os_poll(monkeypatch):
    stream, real_ws, tls, _ = real_parser_transport(monkeypatch, [
        server_frame(b"\x01\xffstdout"), server_frame(b"\x02stderr"),
        server_frame(b'\x03{"status":"Success"}'), server_frame(b"\x03\xe8", opcode=8)])
    stream.update(timeout=1)
    assert stream.read_stdout(timeout=0) == b"\xffstdout"
    stream.update(timeout=1)
    assert stream.read_stderr(timeout=0) == b"stderr"
    stream.update(timeout=1)
    assert stream._channels[3] == b'{"status":"Success"}'
    stream.update(timeout=1)
    assert not stream.is_open() and stream.returncode == 0
    assert real_ws.sock is tls and tls.timeout == 5
    assert tls.sent  # Real parser emitted the close acknowledgement.


@pytest.mark.parametrize("split", [1, 3])
def test_real_parser_retains_partial_header_or_payload_after_read_timeout(monkeypatch, split):
    frame = server_frame(b"\x01preserved")
    stream, _, tls, clock = real_parser_transport(monkeypatch, [frame[:split], None, frame[split:]])
    stream.update(timeout=0.25)
    assert clock.now == 100.25 and stream.read_stdout(timeout=0) == ""
    assert stream.is_open() and tls.timeout == 5
    stream.update(timeout=0.25)
    assert stream.read_stdout(timeout=0) == b"preserved"


def test_real_parser_fragmented_trickle_has_one_slice_deadline(monkeypatch):
    frames = [server_frame(b"\x01a", fin=0)] + [server_frame(b"b", opcode=0, fin=0)] * 20
    stream, _, tls, clock = real_parser_transport(monkeypatch, frames, recv_cost=0.1)
    stream.update(timeout=0.5)
    assert clock.now == pytest.approx(100.5)
    assert stream.is_open() and stream.read_stdout(timeout=0) == ""
    assert max(tls.received_timeouts) <= 0.5 and tls.timeout == 5
    tls.chunks.append(server_frame(b"c", opcode=0))
    for _ in range(30):
        stream.update(timeout=0.5)
        if stream._channels.get(1):
            break
    assert stream.read_stdout(timeout=0) == b"a" + b"b" * 20 + b"c"


@pytest.mark.parametrize("opcode,payload", [(9, b"ping"), (8, b"\x03\xe8")])
@pytest.mark.parametrize("recv_cost", [0, 0.1])
def test_real_parser_control_partial_send_timeout_closes_without_sql_replay(
    monkeypatch, opcode, payload, recv_cost
):
    m = implementation()
    stream, real_ws, tls, clock = real_parser_transport(
        monkeypatch, [server_frame(payload, opcode=opcode)], recv_cost=recv_cost,
        send_cost=0.2, send_size=1)
    with pytest.raises(m.MigrationIOError, match="migration_exec_control_send_failed"):
        stream.update(timeout=0.5)
    assert clock.now == pytest.approx(100.5)
    assert not stream.is_open() and not real_ws.connected and tls._closed
    assert len(tls.sent) == (2 if recv_cost == 0 else 1)
    assert max(tls.sent_timeouts) <= 0.5 - 2 * recv_cost + 1e-10
    calls = len(tls.sent_timeouts)
    stream.update(timeout=1)
    assert len(tls.sent_timeouts) == calls  # No re-emission of consumed control frame or SQL.


def test_real_parser_stdin_partial_send_has_one_five_second_budget(monkeypatch):
    from kubernetes.stream.ws_client import WSClient

    m = implementation()
    stream, real_ws, tls, clock = real_parser_transport(monkeypatch, [], send_cost=2, send_size=1)
    original_write = WSClient.write_stdin
    dispatches = []

    def dispatch(self, data):
        dispatches.append(data)
        return original_write(self, data)

    monkeypatch.setattr(WSClient, "write_stdin", dispatch)
    with pytest.raises(m.MigrationIOError, match="migration_exec_write_outcome_unknown"):
        stream.write_stdin(b"SELECT 1;\n")
    assert dispatches == [b"SELECT 1;\n"]
    assert clock.now == pytest.approx(105)
    assert tls.sent_timeouts == [5, 3, 1] and len(tls.sent) == 2
    assert tls._closed and not stream.is_open() and not real_ws.connected
    with pytest.raises(m.MigrationIOError, match="migration_exec_write_outcome_unknown"):
        stream.write_stdin(b"SELECT 1;\n")
    assert len(dispatches) == 1  # Closed connection cannot redispatch even if a caller tries.


def test_real_parser_stdin_success_preserves_binary_framing_and_timeout(monkeypatch):
    import websocket

    stream, real_ws, tls, _ = real_parser_transport(monkeypatch, [])
    stream.write_stdin(b"SELECT 1;\n")
    expected = websocket.ABNF.create_frame(b"\x00SELECT 1;\n", websocket.ABNF.OPCODE_BINARY)
    expected.get_mask_key = lambda _: b"abcd"
    assert b"".join(tls.sent) == expected.format()
    assert real_ws.sock is tls and tls.timeout == 5


def test_nested_socket_budgets_cannot_extend_outer_deadline(monkeypatch):
    m = implementation()
    _, real_ws, tls, clock = real_parser_transport(monkeypatch, [])
    with m._websocket_budget(real_ws, 1) as outer:
        clock.now += 0.25
        with m._websocket_budget(real_ws, 5) as inner:
            assert inner is outer and inner.gettimeout() == 0.75
            assert inner.pending() == 0 and inner.fileno() == 123
        assert outer.gettimeout() == 0.75
    assert real_ws.sock is tls and tls.timeout == 5


def test_zero_update_does_not_consume_tls_or_advance_clock(monkeypatch):
    stream, _, tls, clock = real_parser_transport(monkeypatch, [server_frame(b"\x01data")])
    stream.update(timeout=0)
    assert not tls.received_timeouts and clock.now == 100


def test_real_parser_explicit_close_is_bounded_across_partial_sends(monkeypatch):
    stream, real_ws, tls, clock = real_parser_transport(monkeypatch, [], send_cost=2, send_size=1)
    stream.close()
    assert clock.now == pytest.approx(103)
    assert tls.sent_timeouts == [3, 1]
    assert tls._closed and not real_ws.connected and not stream.is_open()


def test_received_close_cleanup_releases_raw_socket_without_another_frame(monkeypatch):
    stream, _, tls, _ = real_parser_transport(monkeypatch, [server_frame(b"\x03\xe8", opcode=8)])
    stream.update(timeout=1)
    before = len(tls.sent_timeouts)
    stream.close()
    assert tls._closed and len(tls.sent_timeouts) == before


@pytest.mark.parametrize("stdout,stderr", [(b"row", b""), (b"", b"error"),
                                            (b"", b""), (b"row", b"error")])
@pytest.mark.parametrize("remaining,first_poll", [(-1, 0), (0.25, 0.25), (20, 1)])
def test_real_sdk_channel_drain_never_waits_for_absent_stream(
    monkeypatch, stdout, stderr, remaining, first_poll
):
    from kubernetes.stream.ws_client import STDERR_CHANNEL, STDOUT_CHANNEL, WSClient

    m = implementation()
    client = WSClient.__new__(WSClient)
    client._connected = True
    client._channels = {channel: data for channel, data in (
        (STDOUT_CHANNEL, stdout), (STDERR_CHANNEL, stderr)) if data}
    updates = []

    def bounded_update(timeout=None):
        assert timeout is not None, "real SDK absent channel requested an unbounded wait"
        assert 0 <= timeout <= first_poll
        updates.append(timeout)

    monkeypatch.setattr(client, "update", bounded_update)
    monkeypatch.setattr(m.time, "monotonic", lambda: 100)
    # Keep the real SDK read_stdout/read_stderr/read_channel/peek_channel methods.
    assert m._KubernetesChannel(client).read(100 + remaining) == (stdout, stderr, True)
    assert updates == [first_poll] + [0] * (int(not stdout) + int(not stderr))
    assert not client._channels


def test_administrator_psql_uses_catalog_only_search_path_at_startup():
    import shlex

    options = shlex.split(implementation().psql_command()[1].removeprefix("PGOPTIONS="))
    settings = dict(
        value.split("=", 1)
        for flag, value in zip(options[::2], options[1::2], strict=True)
        if flag == "-c"
    )
    assert settings["search_path"] == "pg_catalog"


@pytest.mark.parametrize(
    "file_contents, accepted",
    [("16\n", True), ("16\r\n", True), ("16", True),
     ("17\n", False), ("16\n17\n", False), (16, False)],
)
def test_pg_identity_normalizes_version_file_line_endings(file_contents, accepted):
    m = implementation()
    payload = {
        "database": "postgres", "databases": ["postgres"],
        "tablespaces": ["pg_default", "pg_global"], "recovery": False,
        "server_version_num": "160015", "pg_version": file_contents,
    }
    session = SimpleNamespace(json=lambda sql: dict(payload))
    if accepted:
        assert m.pg_identity(session)["pg_version"] == "16"
    else:
        with pytest.raises(m.MigrationIOError, match="migration_pg_context_unsupported"):
            m.pg_identity(session)


def test_incomplete_sql_response_is_unknown_and_closed():
    m = implementation()

    class Channel:
        closed = False

        def write(self, data):
            pass

        def read(self, deadline):
            return b"partial\n", b"", False

        def close(self):
            self.closed = True

    channel = Channel()
    session = m.SqlSession(channel)
    with pytest.raises(m.MigrationIOError):
        session.sql("SELECT 1;")
    assert channel.closed


def test_reserved_ledger_cannot_be_business_catalog():
    m = implementation()
    assert m.identifier("sever_orders") == '"sever_orders"'
    for name in ("__omnia_project_migrations", "orders;drop table x", "pg_catalog.x"):
        with pytest.raises(m.MigrationIOError):
            m.identifier(name)


def topology():
    return {
        "Namespace": {"metadata": {"uid": "namespace"}},
        "StatefulSet": {
            "metadata": {"uid": "sts", "labels": {"omnia.project-id": "project"}},
            "spec": {"replicas": 1},
        },
        "PersistentVolumeClaim": {
            "metadata": {"uid": "claim"},
            "status": {"phase": "Bound"},
            "spec": {"volumeName": "pv"},
        },
        "PersistentVolume": {"metadata": {"uid": "volume"}, "spec": {"claimRef": {"uid": "claim"}}},
        "Pod": {
            "metadata": {
                "uid": "pod",
                "name": "project-postgres-0",
                "labels": {"omnia.project-id": "project"},
                "ownerReferences": [{"uid": "sts", "controller": True}],
            },
            "spec": {
                "containers": [
                    {
                        "name": "postgres",
                        "volumeMounts": [{"name": "data", "mountPath": "/var/lib/postgresql/data"}],
                    }
                ],
                "volumes": [
                    {
                        "name": "data",
                        "persistentVolumeClaim": {"claimName": "data-project-postgres-0"},
                    }
                ],
            },
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"name": "postgres", "ready": True, "imageID": "postgres@sha256:" + "a" * 64}
                ],
            },
        },
    }


@pytest.mark.parametrize("changed", ["pvc", "owner", "image", "deleting", "two_pods", "mount"])
def test_target_storage_rejects_ambiguous_or_rebound_resources(changed):
    m = implementation()
    objects = deepcopy(topology())
    if changed == "pvc":
        objects["PersistentVolume"]["spec"]["claimRef"]["uid"] = "other"
    elif changed == "owner":
        objects["Pod"]["metadata"]["ownerReferences"][0]["uid"] = "other"
    elif changed == "image":
        objects["Pod"]["status"]["containerStatuses"][0]["imageID"] = "postgres:16"
    elif changed == "deleting":
        objects["PersistentVolumeClaim"]["metadata"]["deletionTimestamp"] = "now"
    elif changed == "mount":
        objects["Pod"]["spec"]["containers"][0]["volumeMounts"][0]["name"] = "foreign"
    api = SimpleNamespace(
        get=lambda version, kind, name, ns: objects[kind],
        list_objects=lambda *args: [objects["Pod"]] * (2 if changed == "two_pods" else 1),
    )
    pg = m.KubernetesPG(SimpleNamespace(api=api), "project", reader=api)
    with pytest.raises(m.MigrationIOError):
        pg.topology()


def test_target_identity_preserves_volume_and_image_not_only_pod_name():
    m = implementation()
    objects = topology()
    api = SimpleNamespace(
        get=lambda version, kind, name, ns: objects[kind],
        list_objects=lambda *args: [objects["Pod"]],
    )
    result = m.KubernetesPG(SimpleNamespace(api=api), "project", reader=api).topology()
    assert result["pvc_uid"] == "claim"
    assert result["pv_uid"] == "volume"
    assert result["image"] == "postgres@sha256:" + "a" * 64


def test_sql_output_budget_never_returns_partial_success():
    m = implementation()

    class Channel:
        closed = False

        def write(self, data):
            pass

        def read(self, deadline):
            return b"x" * 1024, b"", True

        def close(self):
            self.closed = True

    channel = Channel()
    with pytest.raises(m.MigrationIOError):
        m.SqlSession(channel).sql("SELECT 1;", max_bytes=10)
    assert channel.closed


def test_ledger_view_is_rejected_before_any_relation_read():
    m = implementation()

    class CatalogOnly:
        def json(self, sql):
            assert "FROM public." not in sql
            return {"ordinary": False, "owner": "omnia_project_owner", "columns": []}

    with pytest.raises(m.MigrationIOError):
        m.verify_ledger(CatalogOnly(), required=True)


def test_oversized_table_is_rejected_before_hashing():
    m = implementation()
    from yleum_orchestrator.services.publication_additive_resolver import (
        CatalogColumn,
        CatalogTable,
    )

    table = CatalogTable(
        "public",
        "orders",
        (CatalogColumn("id", "pg_catalog.int8", False, -1),),
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "p",
        True,
        False,
        False,
    )

    class CountOnly:
        def json(self, sql):
            if "pg_catalog.pg_class" in sql:
                return None
            assert "LIMIT" in sql and "sha256" not in sql
            return m.MAX_ROWS + 1

        def sql(self, sql, **kwargs):
            raise AssertionError("over-budget rows must never be hashed")

    with pytest.raises(m.MigrationIOError):
        m.row_proofs(CountOnly(), (table,))


def test_websocket_options_enforce_timeout_and_preserve_tls_identity():
    m = implementation()
    configuration = SimpleNamespace(
        verify_ssl=True,
        assert_hostname=None,
        ssl_ca_cert="ca.pem",
        cert_file="client.pem",
        key_file="client.key",
        tls_server_name="cluster.example",
    )
    options = m.websocket_options(
        configuration,
        "wss://127.0.0.1/exec",
        {"authorization": "synthetic", "ignored": "unused"},
    )
    assert options["timeout"] == 15
    assert options["http_proxy_timeout"] == 15
    assert options["redirect_limit"] == 0
    assert options["http_no_proxy"] == ["*"]
    assert options["sslopt"] == {
        "cert_reqs": ssl.CERT_REQUIRED,
        "ca_certs": "ca.pem",
        "check_hostname": True,
        "certfile": "client.pem",
        "keyfile": "client.key",
        "server_hostname": "cluster.example",
    }
    assert options["header"] == [
        "authorization: synthetic",
        "sec-websocket-protocol: v4.channel.k8s.io",
    ]


@pytest.mark.parametrize(
    "url,verify,hostname",
    [
        ("ws://cluster.example/exec", True, None),
        ("wss://cluster.example/exec", False, None),
        ("wss://cluster.example/exec", True, False),
        ("wss://cluster.example/exec", True, None),
    ],
)
def test_websocket_cannot_disable_tls_or_hostname_validation(url, verify, hostname):
    m = implementation()
    config = SimpleNamespace(verify_ssl=verify, assert_hostname=hostname)
    with pytest.raises(m.MigrationIOError):
        m.websocket_options(config, url, {})


def test_pinned_official_wsclient_accepts_bounded_socket_without_global_patch(monkeypatch):
    kubernetes = pytest.importorskip("kubernetes")
    websocket = pytest.importorskip("websocket")
    m = implementation()
    sent = []

    class Socket:
        def __init__(self):
            self.sock = SimpleNamespace(gettimeout=lambda: 5, settimeout=lambda _: None)

        def settimeout(self, seconds):
            assert seconds == 5

        def send(self, payload, opcode):
            sent.append(payload)

        def close(self, **kwargs):
            pass

        def shutdown(self):
            self.sock = None

    def connect(url, **options):
        assert url.startswith("wss://")
        assert options["timeout"] == options["http_proxy_timeout"] == 15
        assert options["sslopt"]["cert_reqs"] == ssl.CERT_REQUIRED
        assert options["http_no_proxy"] == ["*"]
        return Socket()

    monkeypatch.setattr(websocket, "create_connection", connect)
    config = kubernetes.client.Configuration()
    config.host = "https://127.0.0.1"
    config.no_proxy = "unrelated.example"
    config.proxy_headers = {"ignored": "synthetic"}
    stream = m._websocket_call(
        config, "GET", config.host + "/exec", binary=True, _preload_content=False, headers={}
    )
    stream.write_stdin(b"SELECT 1;\n")
    assert sent == [b"\x00SELECT 1;\n"]
    stream._channels[3] = b'{"status":"Success"}'
    stream._connected = False
    assert stream.returncode == 0
    stream.close()


def test_dedicated_metadata_reader_passes_finite_timeout_and_serializes_uid(monkeypatch):
    kubernetes = pytest.importorskip("kubernetes")
    m = implementation()
    config = kubernetes.client.Configuration()
    config.host = "https://127.0.0.1"
    reader = m._KubernetesReader(config)
    try:

        def read(name, namespace, **kwargs):
            assert kwargs == {"_request_timeout": (10, 20)}
            return kubernetes.client.V1Pod(metadata=kubernetes.client.V1ObjectMeta(uid="owned"))

        monkeypatch.setattr(reader.core, "read_namespaced_pod", read)
        assert reader.get("v1", "Pod", "postgres", "owned")["metadata"]["uid"] == "owned"
        assert reader.configuration.retries == 0
        assert reader.configuration is not config
    finally:
        reader.client.close()


def test_explicit_proxy_dns_is_rejected_without_using_ambient_proxy():
    m = implementation()
    config = SimpleNamespace(
        verify_ssl=True,
        assert_hostname=None,
        ssl_ca_cert="ca.pem",
        proxy="http://proxy.example:8080",
    )
    with pytest.raises(m.MigrationIOError):
        m.websocket_options(config, "wss://127.0.0.1/exec", {})


@pytest.mark.parametrize(
    "variable", ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
)
def test_ambient_proxy_fails_closed_before_real_sdk_drops_no_proxy(monkeypatch, variable):
    kubernetes = pytest.importorskip("kubernetes")
    pytest.importorskip("websocket")
    from websocket._http import proxy_info
    from websocket._url import get_proxy_info

    m = implementation()
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(variable, "http://ambient.example:8080")
    proxy = proxy_info(http_no_proxy=["*"])
    assert proxy.no_proxy is None
    if variable.lower() == "https_proxy":
        assert get_proxy_info("127.0.0.1", True, no_proxy=proxy.no_proxy)[0] == "ambient.example"
    config = kubernetes.client.Configuration()
    config.host = "https://127.0.0.1"
    with pytest.raises(m.MigrationIOError, match="migration_ambient_proxy_unsupported"):
        m.websocket_options(config, "wss://127.0.0.1/exec", {})


@pytest.mark.parametrize(
    "host,proxy",
    [
        ("https://cluster.example", None),
        ("https://127.0.0.1", "http://proxy.example:8080"),
        ("http://127.0.0.1", None),
    ],
)
def test_metadata_transport_rejects_unbounded_resolution_before_client_creation(
    monkeypatch, host, proxy
):
    kubernetes = pytest.importorskip("kubernetes")
    m = implementation()
    config = kubernetes.client.Configuration()
    config.host = host
    config.proxy = proxy

    def forbidden(*args, **kwargs):
        pytest.fail("invalid endpoint reached SDK client construction")

    monkeypatch.setattr(kubernetes.client, "ApiClient", forbidden)
    with pytest.raises(m.MigrationIOError):
        m._KubernetesReader(config)
