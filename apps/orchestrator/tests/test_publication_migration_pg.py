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
        def settimeout(self, seconds):
            assert seconds == 5

        def send(self, payload, opcode):
            sent.append(payload)

        def close(self, **kwargs):
            pass

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
