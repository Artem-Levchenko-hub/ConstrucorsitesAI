"""Bounded controller PG transport. No credentials, SQL or rows in errors."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import ssl
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

from yleum_orchestrator.services.project_database_schema_proof import legacy_schema_digest
from yleum_orchestrator.services.publication_additive_resolver import (
    CatalogColumn,
    CatalogTable,
    catalog_digest,
)
from yleum_orchestrator.services.restoration_database import TRUSTED_ADMIN_PGOPTIONS

MAX_OUTPUT = 8 * 1024**2
MAX_DUMP = 64 * 1024**2
MAX_ROWS = 100_000
LEDGER = "__omnia_project_migrations"
MIGRATION_PGOPTIONS = (
    TRUSTED_ADMIN_PGOPTIONS + " -c timezone=UTC -c datestyle=ISO,YMD -c extra_float_digits=3"
    " -c statement_timeout=45000 -c lock_timeout=5000"
    # Set this at startup, before even catalog probes can resolve public overloads.
    " -c search_path=pg_catalog"
)


class MigrationIOError(ValueError):
    """Only static error codes cross the controller boundary."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise MigrationIOError(code)


PUBLIC_SCHEMA_OWNER_STATEMENTS = {
    "postgres": 'ALTER SCHEMA public OWNER TO "postgres";',
    "pg_database_owner": 'ALTER SCHEMA public OWNER TO "pg_database_owner";',
    "omnia_project_owner": 'ALTER SCHEMA public OWNER TO "omnia_project_owner";',
}


def validated_public_schema_owner(value: object) -> str:
    require(
        type(value) is str and value in PUBLIC_SCHEMA_OWNER_STATEMENTS,
        "migration_public_schema_owner_unsupported",
    )
    return cast(str, value)


def read_public_schema_owner(session: Any, *, standalone: bool = True) -> str:
    # In an existing transaction the caller must already hold the roles lock.
    # Otherwise use ONE exec/transaction (DockerPG calls use separate psqls).
    # The lock and catalog SELECT must be separate statements: a SELECT's
    # snapshot predates any wait inside it, even with a MATERIALIZED lock CTE.
    query = (
        "SELECT pg_catalog.to_json(CASE WHEN db_owner.rolname='postgres' "
        "THEN owner.rolname ELSE '' END) FROM pg_catalog.pg_namespace n "
        "JOIN pg_catalog.pg_roles owner ON owner.oid=n.nspowner "
        "JOIN pg_catalog.pg_database d ON d.datname=pg_catalog.current_database() "
        "JOIN pg_catalog.pg_roles db_owner ON db_owner.oid=d.datdba "
        "WHERE n.nspname='public';"
    )
    if standalone:
        query = (
            "BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY; "
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtext('omnia:project:roles:v1')); " + query + " COMMIT;"
        )
    return validated_public_schema_owner(session.json(query))


def _literal_endpoint(url: str, schemes: tuple[str, ...]) -> None:
    """Socket timeouts cannot bound DNS; accept only addresses needing no resolution."""
    try:
        endpoint = urlsplit(url)
        valid = endpoint.scheme in schemes and endpoint.hostname is not None
        require(valid, "migration_endpoint_unsupported")
        ipaddress.ip_address(endpoint.hostname or "")
        require("%" not in (endpoint.hostname or ""), "migration_endpoint_unsupported")
        _ = endpoint.port  # Reject malformed ports before entering a transport.
    except (TypeError, ValueError):
        raise MigrationIOError("migration_endpoint_unsupported") from None


def _transport_policy(configuration: Any, url: str, scheme: str) -> None:
    require(
        configuration.verify_ssl is True and configuration.assert_hostname is not False,
        "migration_tls_required",
    )
    _literal_endpoint(url, (scheme,))
    proxy = getattr(configuration, "proxy", None)
    if proxy:
        _literal_endpoint(proxy, ("http", "https"))
    else:
        # websocket-client drops http_no_proxy without an explicit proxy_host.
        # Reject ambient configuration rather than mutating process-global env.
        require(
            not any(
                os.environ.get(name)
                for name in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                )
            ),
            "migration_ambient_proxy_unsupported",
        )


def websocket_options(configuration: Any, url: str, headers: dict[str, str]) -> dict[str, Any]:
    """TLS policy from Kubernetes 33.1.0 create_websocket, with finite socket timeouts."""
    import certifi

    _transport_policy(configuration, url, "wss")
    ssl_options: dict[str, Any] = {
        "cert_reqs": ssl.CERT_REQUIRED,
        "ca_certs": configuration.ssl_ca_cert or certifi.where(),
        "check_hostname": True,
    }
    for field, option in (
        ("cert_file", "certfile"),
        ("key_file", "keyfile"),
        ("tls_server_name", "server_hostname"),
    ):
        if getattr(configuration, field, None):
            ssl_options[option] = getattr(configuration, field)
    sent_headers = []
    if headers.get("authorization"):
        sent_headers.append("authorization: " + headers["authorization"])
    protocol = headers.get("sec-websocket-protocol", "v4.channel.k8s.io")
    require(protocol == "v4.channel.k8s.io", "migration_exec_protocol_unsupported")
    sent_headers.append("sec-websocket-protocol: " + protocol)
    options = {
        "timeout": 15,
        "http_proxy_timeout": 15,
        "redirect_limit": 0,
        "sslopt": ssl_options,
        "header": sent_headers,
        "skip_utf8_validation": False,
    }
    if not getattr(configuration, "proxy", None):
        options["http_no_proxy"] = ["*"]
    return options


class _DeadlineSocket:
    """Forward the connected TLS socket while sharing one absolute I/O deadline."""

    def __init__(self, socket: Any, deadline: float) -> None:
        self.socket = socket
        self.deadline = deadline
        self.send_attempted = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.socket, name)

    def gettimeout(self) -> float:
        # websocket-client selects with this value after SSLWantRead/Write.
        # Zero makes its direct recv/send enter our deadline guard, never wait.
        return max(0, self.deadline - time.monotonic())

    def _arm(self) -> None:
        from websocket import WebSocketTimeoutException

        remaining = self.gettimeout()
        if remaining <= 0:
            raise WebSocketTimeoutException("migration_exec_slice_expired")
        self.socket.settimeout(remaining)

    def recv(self, size: int, *args: Any, **kwargs: Any) -> Any:
        self._arm()
        return self.socket.recv(size, *args, **kwargs)

    def send(self, data: Any, *args: Any, **kwargs: Any) -> Any:
        self.send_attempted = True
        self._arm()
        return self.socket.send(data, *args, **kwargs)


@contextmanager
def _websocket_budget(websocket: Any, seconds: float) -> Iterator[_DeadlineSocket]:
    raw = websocket.sock
    require(raw is not None, "migration_exec_closed")
    deadline = time.monotonic() + seconds
    if isinstance(raw, _DeadlineSocket):
        previous_deadline = raw.deadline
        raw.deadline = min(previous_deadline, deadline)
        try:
            yield raw
        finally:
            raw.deadline = previous_deadline
        return
    ordinary_timeout = raw.gettimeout()
    proxy = _DeadlineSocket(raw, deadline)
    websocket.sock = proxy
    try:
        yield proxy
    finally:
        # A hard shutdown must never be undone by restoring a dead socket.
        if websocket.sock is proxy:
            websocket.sock = raw
            try:
                raw.settimeout(ordinary_timeout)
            except OSError:
                websocket.shutdown()
                raise MigrationIOError("migration_exec_timeout_restore_failed") from None


def _websocket_call(configuration: Any, method: str, url: str, **kwargs: Any) -> Any:
    """Per-instance official WSClient framing, pinned to its checked initialization contract.

    Upstream 33.1.0 create_websocket omits the connect timeout. Override only this
    instance's connection creation and bounded frame polling, retaining official
    frame/channel/exit parsing and proxy authentication. No shared client mutation.

    Initialization/TLS/channel-routing adaptation: Copyright 2018 The Kubernetes Authors,
    Apache License 2.0, https://www.apache.org/licenses/LICENSE-2.0 .
    https://github.com/kubernetes-client/python/blob/v33.1.0/kubernetes/base/stream/ws_client.py
    Distributed AS IS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.
    """
    import kubernetes
    import websocket
    from kubernetes.stream import ws_client

    require(kubernetes.__version__ == "33.1.0", "migration_sdk_version_unsupported")
    require(websocket.__version__ == "1.9.2", "migration_websocket_version_unsupported")
    require(
        kwargs.get("_preload_content") is False and kwargs.get("binary") is True,
        "migration_stream_mode_unsupported",
    )
    endpoint = ws_client.get_websocket_url(url, kwargs.get("query_params"))
    options = websocket_options(configuration, endpoint, kwargs.get("headers") or {})
    if configuration.proxy or configuration.proxy_headers:
        options = ws_client.websocket_proxycare(
            options, configuration, endpoint, kwargs.get("headers")
        )
    if not configuration.proxy:
        # Defense in depth; _transport_policy rejects ambient proxies because
        # websocket-client 1.9.2 discards this option without an explicit host.
        options["http_no_proxy"] = ["*"]
    websocket.enableTrace(False)  # Same upstream suppression of authenticated wire traces.

    class Discard:
        def write(self, data: bytes) -> None:
            pass

    class BoundedWSClient(ws_client.WSClient):  # type: ignore[misc]  # upstream has no typing
        def __init__(self) -> None:
            self._connected = False
            self._channels: dict[int, bytes] = {}
            self.binary = True
            self.newline = b"\n"
            self._all = Discard()  # Only channel buffers; never duplicate a full private dump.
            self._returncode = None
            self.sock = websocket.create_connection(endpoint, **options)
            self.sock.settimeout(5)
            self._connected = True

        def _abort(self) -> None:
            self._connected = False
            # SDK immediate shutdown performs no close handshake/protocol writes.
            self.sock.shutdown()  # type: ignore[no-untyped-call]

        def update(self, timeout: float = 0) -> None:
            if not self.is_open():
                return
            if not self.sock.connected:
                self._connected = False
                return
            require(
                isinstance(timeout, (int, float)) and math.isfinite(timeout) and timeout >= 0,
                "migration_exec_timeout_required",
            )
            if timeout == 0:
                return  # Buffered channel drains must not perform additional I/O.
            with _websocket_budget(self.sock, min(timeout, 1)) as budget:
                try:
                    opcode, frame = self.sock.recv_data_frame(control_frame=True)
                except websocket.WebSocketTimeoutException:
                    if budget.send_attempted:
                        self._abort()
                        raise MigrationIOError("migration_exec_control_send_failed") from None
                    return  # Real parser retains partial header/payload/continuation state.
                except Exception:
                    self._abort()
                    raise MigrationIOError("migration_exec_read_failed") from None
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                self._connected = False
            elif opcode in (websocket.ABNF.OPCODE_BINARY, websocket.ABNF.OPCODE_TEXT):
                data = frame.data
                if len(data) > 1:
                    channel, payload = data[0], data[1:]
                    if channel in (ws_client.STDOUT_CHANNEL, ws_client.STDERR_CHANNEL):
                        self._all.write(payload)
                    self._channels[channel] = self._channels.get(channel, b"") + payload

        def write_stdin(self, data: bytes) -> None:
            try:
                with _websocket_budget(self.sock, 5):
                    # Exactly one dispatch; partial sends share the budget.
                    super().write_stdin(data)
            except Exception:
                self._abort()
                raise MigrationIOError("migration_exec_write_outcome_unknown") from None

        def close(self, **kwargs: Any) -> None:
            self._connected = False
            try:
                if self.sock.sock is not None:
                    with _websocket_budget(self.sock, 3):
                        self.sock.close(**kwargs)
            finally:
                # Upstream close skips raw cleanup after a peer CLOSE set connected=False.
                self.sock.shutdown()  # type: ignore[no-untyped-call]

    return BoundedWSClient()


class _KubernetesReader:
    """Dedicated typed GET client; no discovery requests or unbounded SDK retries."""

    def __init__(self, configuration: Any) -> None:
        import kubernetes
        from kubernetes import client

        require(kubernetes.__version__ == "33.1.0", "migration_sdk_version_unsupported")
        _transport_policy(configuration, configuration.host, "https")
        self.configuration = deepcopy(configuration)
        self.configuration.retries = 0
        self.configuration.debug = False
        self.client = client.ApiClient(self.configuration)
        self.core = client.CoreV1Api(self.client)
        self.apps = client.AppsV1Api(self.client)

    def get(self, version: str, kind: str, name: str, namespace: str | None) -> dict[str, Any]:
        methods = {
            ("v1", "Namespace"): (self.core.read_namespace, (name,)),
            ("apps/v1", "StatefulSet"): (self.apps.read_namespaced_stateful_set, (name, namespace)),
            ("v1", "Pod"): (self.core.read_namespaced_pod, (name, namespace)),
            ("v1", "PersistentVolumeClaim"): (
                self.core.read_namespaced_persistent_volume_claim,
                (name, namespace),
            ),
            ("v1", "PersistentVolume"): (self.core.read_persistent_volume, (name,)),
        }
        require((version, kind) in methods, "migration_resource_kind_unsupported")
        method, args = methods[(version, kind)]
        try:
            return dict(
                self.client.sanitize_for_serialization(method(*args, _request_timeout=(10, 20)))
            )
        except Exception:
            raise MigrationIOError("migration_resource_read_failed") from None

    def list_objects(
        self, version: str, kind: str, namespace: str, selector: str
    ) -> list[dict[str, Any]]:
        require((version, kind) == ("v1", "Pod"), "migration_resource_kind_unsupported")
        try:
            found = self.core.list_namespaced_pod(
                namespace, label_selector=selector, limit=3, _request_timeout=(10, 20)
            )
            require(not getattr(found.metadata, "_continue", None), "migration_pod_ambiguous")
            return [dict(self.client.sanitize_for_serialization(item)) for item in found.items]
        except Exception:
            raise MigrationIOError("migration_resource_read_failed") from None


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def identifier(name: str, *, ledger: bool = False) -> str:
    require(
        (isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) is not None)
        or (ledger and name == LEDGER),
        "migration_identifier_unsupported",
    )
    return '"' + name + '"'


def psql_command(seconds: int = 180) -> list[str]:
    return [
        "env",
        "PGOPTIONS=" + MIGRATION_PGOPTIONS,
        "timeout",
        "-s",
        "KILL",
        str(seconds),
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        "VERBOSITY=sqlstate",
        "-h",
        "/tmp",
        "-U",
        "postgres",
        "-d",
        "postgres",
    ]


class SqlSession(AbstractContextManager["SqlSession"]):
    def __init__(self, channel: Any, *, seconds: int = 180) -> None:
        self.channel = channel
        self.deadline = time.monotonic() + seconds
        self.broken = False
        self.pod_uid: str | None = None

    def sql(self, sql: str, *, max_bytes: int = MAX_OUTPUT) -> bytes:
        require(
            not self.broken and len(sql.encode()) <= MAX_OUTPUT, "migration_session_unavailable"
        )
        marker = ("omnia_" + uuid4().hex).encode()
        output, errors = bytearray(), bytearray()
        try:
            self.channel.write(sql.encode() + b"\n\\echo " + marker + b"\n")
            while time.monotonic() < self.deadline:
                stdout, stderr, alive = self.channel.read(self.deadline)
                output.extend(stdout)
                errors.extend(stderr)
                require(
                    len(output) <= max_bytes + 64 and len(errors) <= 8192, "migration_output_budget"
                )
                # stderr can contain malicious notices. Never expose it, even on success.
                require(not errors, "migration_sql_diagnostic")
                lines = bytes(output).splitlines(keepends=True)
                if lines and lines[-1].rstrip(b"\r\n") == marker:
                    result = b"".join(lines[:-1])
                    require(len(result) <= max_bytes, "migration_output_budget")
                    return result
                require(alive, "migration_sql_outcome_unknown")
            raise MigrationIOError("migration_sql_deadline")
        except Exception:
            self.broken = True
            self.close()
            raise MigrationIOError("migration_sql_outcome_unknown") from None

    def json(self, sql: str) -> Any:
        try:
            return json.loads(self.sql(sql))
        except Exception:
            raise MigrationIOError("migration_observation_invalid") from None

    def close(self) -> None:
        self.channel.close()

    def __exit__(self, *_: Any) -> None:
        self.close()


class _KubernetesChannel:
    def __init__(self, client: Any) -> None:
        self.client = client

    def write(self, data: bytes) -> None:
        self.client.write_stdin(data)

    def read(self, deadline: float) -> tuple[bytes, bytes, bool]:
        self.client.update(timeout=max(0, min(1, deadline - time.monotonic())))
        # SDK stream readers default to an unbounded wait when their channel is empty.
        # Only the initial poll may wait; draining either stream must stay nonblocking.
        return (
            self.client.read_stdout(timeout=0) or b"",
            self.client.read_stderr(timeout=0) or b"",
            self.client.is_open(),
        )

    def close(self) -> None:
        self.client.close()


class KubernetesPG:
    def __init__(self, runtime: Any, project_id: str, *, reader: Any = None) -> None:
        self.runtime = runtime
        self.reader = (
            reader
            if reader is not None
            else _KubernetesReader(runtime.api._core.api_client.configuration)
        )
        self.project_id = project_id
        self.namespace = "app-" + project_id
        self.pod: str | None = None
        self.pod_uid: str | None = None

    def _stream(self, command: list[str], *, stdin: bool) -> Any:
        from kubernetes import client
        from kubernetes.stream.stream import _websocket_request

        require(self.pod is not None, "migration_target_not_identified")
        # stream temporarily replaces ApiClient.request: never share the REST client.
        configuration = deepcopy(self.reader.configuration)
        core = client.CoreV1Api(client.ApiClient(configuration))
        try:
            return _websocket_request(
                _websocket_call,
                None,
                core.connect_get_namespaced_pod_exec,
                self.pod,
                self.namespace,
                container="postgres",
                command=command,
                stdin=stdin,
                stdout=True,
                stderr=True,
                tty=False,
                _preload_content=False,
                binary=True,
                _request_timeout=(10, 20),
            )
        except Exception:
            raise MigrationIOError("migration_exec_unavailable") from None
        finally:
            core.api_client.close()

    def session(self) -> SqlSession:
        expected_uid = self.pod_uid
        require(bool(expected_uid), "migration_pod_identity_unavailable")
        session = SqlSession(_KubernetesChannel(self._stream(psql_command(), stdin=True)))
        try:
            pod = self.reader.get("v1", "Pod", cast(str, self.pod), self.namespace)
            require(
                pod.get("metadata", {}).get("uid") == expected_uid, "migration_pod_identity_changed"
            )
            session.pod_uid = expected_uid
            return session
        except Exception:
            session.close()
            raise MigrationIOError("migration_pod_identity_changed") from None

    def command(self, argv: list[str], *, max_bytes: int = MAX_OUTPUT) -> bytes:
        channel = _KubernetesChannel(
            self._stream(
                [
                    "env",
                    "PGOPTIONS=" + MIGRATION_PGOPTIONS,
                    "timeout",
                    "-s",
                    "KILL",
                    "180",
                    *argv,
                ],
                stdin=False,
            )
        )
        output = bytearray()
        deadline = time.monotonic() + 190
        try:
            while time.monotonic() < deadline:
                stdout, stderr, alive = channel.read(deadline)
                output.extend(stdout)
                require(not stderr and len(output) <= max_bytes, "migration_dump_failed")
                if not alive:
                    require(channel.client.returncode == 0, "migration_dump_failed")
                    return bytes(output)
            raise MigrationIOError("migration_dump_deadline")
        finally:
            channel.close()

    def topology(self) -> dict[str, Any]:
        api, namespace = self.reader, self.namespace

        def get(version: str, kind: str, name: str, ns: str | None) -> dict[str, Any]:
            return dict(api.get(version, kind, name, ns) or {})

        ns = get("v1", "Namespace", namespace, None)
        sts = get("apps/v1", "StatefulSet", "project-postgres", namespace)
        pods = api.list_objects(
            "v1", "Pod", namespace, "app.kubernetes.io/component=project-postgres"
        )
        require(len(pods) == 1, "migration_pod_ambiguous")
        pod = pods[0]
        sm, pm = sts.get("metadata", {}), pod.get("metadata", {})
        require(
            all(
                x.get("uid") and not x.get("deletionTimestamp")
                for x in (ns.get("metadata", {}), sm, pm)
            ),
            "migration_target_identity",
        )
        require(
            sm.get("labels", {}).get("omnia.project-id") == self.project_id
            and pm.get("labels", {}).get("omnia.project-id") == self.project_id,
            "migration_target_owner",
        )
        require(
            any(
                x.get("uid") == sm["uid"] and x.get("controller") is True
                for x in pm.get("ownerReferences", [])
            ),
            "migration_target_owner",
        )
        require(
            sts.get("spec", {}).get("replicas") == 1
            and pod.get("status", {}).get("phase") == "Running",
            "migration_target_not_ready",
        )
        volumes = pod.get("spec", {}).get("volumes", [])
        containers = [
            c for c in pod.get("spec", {}).get("containers", []) if c.get("name") == "postgres"
        ]
        require(len(containers) == 1, "migration_postgres_container_ambiguous")
        mounts = [
            m
            for m in containers[0].get("volumeMounts", [])
            if m.get("mountPath") == "/var/lib/postgresql/data"
        ]
        require(
            len(mounts) == 1
            and mounts[0].get("name") == "data"
            and not mounts[0].get("subPath")
            and not mounts[0].get("subPathExpr"),
            "migration_postgres_storage_redirected",
        )
        claims = [
            x.get("persistentVolumeClaim", {}).get("claimName")
            for x in volumes
            if x.get("name") == "data"
        ]
        require(claims == ["data-project-postgres-0"], "migration_claim_changed")
        pvc = get("v1", "PersistentVolumeClaim", claims[0], namespace)
        pv_name = pvc.get("spec", {}).get("volumeName")
        require(
            bool(pv_name) and pvc.get("status", {}).get("phase") == "Bound",
            "migration_claim_not_bound",
        )
        pv = get("v1", "PersistentVolume", pv_name, None)
        cm, vm = pvc.get("metadata", {}), pv.get("metadata", {})
        require(
            cm.get("uid")
            and vm.get("uid")
            and not cm.get("deletionTimestamp")
            and not vm.get("deletionTimestamp")
            and pv.get("spec", {}).get("claimRef", {}).get("uid") == cm["uid"],
            "migration_volume_identity",
        )
        statuses = [
            x
            for x in pod.get("status", {}).get("containerStatuses", [])
            if x.get("name") == "postgres"
        ]
        require(
            len(statuses) == 1 and statuses[0].get("ready") is True, "migration_database_not_ready"
        )
        image = statuses[0].get("imageID", "").removeprefix("docker-pullable://")
        require(
            re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image) is not None,
            "migration_running_image_unpinned",
        )
        self.pod = pm["name"]
        self.pod_uid = pm["uid"]
        return {
            "namespace": namespace,
            "namespace_uid": ns["metadata"]["uid"],
            "statefulset_uid": sm["uid"],
            "pvc_name": claims[0],
            "pvc_uid": cm["uid"],
            "pv_name": pv_name,
            "pv_uid": vm["uid"],
            "image": image,
        }

    def identify(self) -> dict[str, Any]:
        topology = self.topology()
        with self.session() as session:
            identity = pg_identity(session)
            require(
                session.json("SELECT to_json(current_setting('data_directory'));")
                == "/var/lib/postgresql/data",
                "migration_postgres_storage_redirected",
            )
        require(self.topology() == topology, "migration_topology_changed")
        require(
            self.command(["id", "-u", "postgres"], max_bytes=16).strip() == b"70",
            "migration_postgres_user_unsupported",
        )
        return {**topology, **identity}

    def full_schema(self) -> str:
        return legacy_schema_digest(self.command(schema_command()).decode("utf-8"))

    def globals(self) -> bytes:
        return self.command(
            ["pg_dumpall", "--globals-only", "--no-role-passwords", "-h", "/tmp", "-U", "postgres"]
        )


def schema_command() -> list[str]:
    return [
        "pg_dumpall",
        "--schema-only",
        "--no-role-passwords",
        "--no-owner",
        "--no-privileges",
        "-h",
        "/tmp,/var/run/postgresql",
        "-U",
        "postgres",
    ]


def pg_identity(session: Any) -> dict[str, Any]:
    result = session.json("""
SELECT json_build_object(
 'system_id',(SELECT system_identifier::text FROM pg_catalog.pg_control_system()),
 'database',current_database(),
 'database_oid',(SELECT oid::text FROM pg_catalog.pg_database WHERE datname=current_database()),
 'server_version_num',current_setting('server_version_num'),
 'pg_version',pg_catalog.pg_read_file(current_setting('data_directory')||'/PG_VERSION'),
 'timeline',(SELECT timeline_id FROM pg_catalog.pg_control_checkpoint()),
 'previous_timeline',(SELECT prev_timeline_id FROM pg_catalog.pg_control_checkpoint()),
 'history',(SELECT CASE WHEN timeline_id=1 THEN '' ELSE
  encode(sha256(convert_to(pg_catalog.pg_read_file('pg_wal/'||
  lpad(upper(to_hex(timeline_id)),8,'0')||'.history'),'UTF8')),'hex') END
  FROM pg_catalog.pg_control_checkpoint()),
 'recovery',pg_catalog.pg_is_in_recovery(),
 'databases',(SELECT json_agg(datname ORDER BY datname) FROM pg_catalog.pg_database
  WHERE NOT datistemplate),
 'tablespaces',(SELECT json_agg(spcname ORDER BY spcname) FROM pg_catalog.pg_tablespace));
""")
    # PG_VERSION is a line-oriented file. SQL trim(text) removes spaces only.
    pg_version = result.get("pg_version")
    if isinstance(pg_version, str):
        result["pg_version"] = pg_version.strip()
    require(
        result.get("database") == "postgres"
        and result.get("databases") == ["postgres"]
        and result.get("tablespaces") == ["pg_default", "pg_global"]
        and result.get("recovery") is False
        and 160000 <= int(result.get("server_version_num", 0)) < 170000
        and result.get("pg_version") == "16",
        "migration_pg_context_unsupported",
    )
    return dict(result)


def verify_ledger(session: Any, *, required: bool = False) -> bool:
    """Never resolve generated views/types/functions while reading the reserved journal."""
    row = session.json("""SELECT json_build_object(
      'ordinary', c.relkind='r' AND c.relpersistence='p' AND NOT c.relispartition
       AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules
       AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits WHERE inhrelid=c.oid OR inhparent=c.oid)
       AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid=c.oid AND NOT tgisinternal),
      'owner',pg_catalog.pg_get_userbyid(c.relowner),
      'columns',(SELECT json_agg(json_build_array(a.attname,n.nspname,t.typname,a.atttypmod,
       a.attnotnull,a.attgenerated,a.attidentity,pg_catalog.pg_get_expr(d.adbin,d.adrelid),
       a.attcollation=t.typcollation,a.attisdropped) ORDER BY a.attnum)
       FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
       JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
       LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
       WHERE a.attrelid=c.oid AND a.attnum>0),
      'constraints',(SELECT json_agg(pg_catalog.pg_get_constraintdef(oid) ORDER BY conname)
       FROM pg_catalog.pg_constraint WHERE conrelid=c.oid),
      'indexes',(SELECT count(*)=1 AND bool_and(indisprimary AND indisvalid AND indisunique)
       FROM pg_catalog.pg_index WHERE indrelid=c.oid))
      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace ns ON ns.oid=c.relnamespace
      WHERE ns.nspname='public' AND c.relname='__omnia_project_migrations';""")
    if row is None:
        require(not required, "migration_ledger_missing")
        return False
    require(
        isinstance(row, dict)
        and row.get("ordinary") is True
        and row.get("owner") in {"postgres", "omnia_project_owner"}
        and row.get("indexes") is True
        and row.get("constraints") == ["PRIMARY KEY (name)"],
        "migration_ledger_identity_invalid",
    )
    columns = row.get("columns")
    require(isinstance(columns, list) and len(columns) == 3, "migration_ledger_shape_invalid")
    expected = [
        ["name", "pg_catalog", "text", -1, True, "", "", None, True, False],
        ["sha256", "pg_catalog", "text", -1, True, "", "", None, True, False],
        ["applied_at", "pg_catalog", "timestamptz", -1, True, "", "", "now()", True, False],
    ]
    if columns[2][7] == "pg_catalog.now()":
        columns[2][7] = "now()"
    require(columns == expected, "migration_ledger_shape_invalid")
    return True


def read_catalog(session: Any) -> tuple[CatalogTable, ...]:
    verify_ledger(session)
    require(
        session.json("""SELECT to_json(NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.relkind IN ('r','p','f','m') AND c.relpersistence<>'t'
      AND n.nspname<>'public' AND n.nspname<>'information_schema' AND n.nspname !~ '^pg_'));""")
        is True,
        "migration_nonpublic_data_unsupported",
    )
    rows = session.json("""
SELECT coalesce(json_agg(json_build_object(
 'name',c.relname,'persistence',c.relpersistence,
 'plain',c.relkind='r' AND NOT c.relispartition AND NOT EXISTS
  (SELECT 1 FROM pg_catalog.pg_inherits WHERE inhrelid=c.oid OR inhparent=c.oid),
 'rls',c.relrowsecurity,'force',c.relforcerowsecurity,
 'columns',(SELECT json_agg(json_build_object('name',a.attname,'type',tn.nspname||'.'||t.typname,
  'nullable',NOT a.attnotnull,'typmod',a.atttypmod,'default',pg_get_expr(d.adbin,d.adrelid),
  'generated',a.attgenerated<>'','identity',a.attidentity<>'',
  'collation',CASE WHEN a.attcollation=t.typcollation THEN NULL ELSE
   (SELECT nspname||'.'||collname FROM pg_catalog.pg_collation co JOIN pg_catalog.pg_namespace nn
    ON nn.oid=co.collnamespace WHERE co.oid=a.attcollation) END) ORDER BY a.attnum)
  FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
  JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
  LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
 'constraints',(SELECT json_agg(pg_get_constraintdef(oid) ORDER BY conname)
  FROM pg_catalog.pg_constraint WHERE conrelid=c.oid),
 'indexes',(SELECT json_agg(indexdef ORDER BY indexname) FROM pg_catalog.pg_indexes
  WHERE schemaname='public' AND tablename=c.relname),
 'policies',(SELECT json_agg(json_build_array(polname,polcmd,polpermissive,
  (SELECT json_agg(CASE WHEN r=0 THEN 'PUBLIC' ELSE pg_get_userbyid(r)::text END
   ORDER BY CASE WHEN r=0 THEN 'PUBLIC' ELSE pg_get_userbyid(r)::text END)
   FROM unnest(polroles) r), pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid))
  ORDER BY polname) FROM pg_catalog.pg_policy WHERE polrelid=c.oid),
 'triggers',(SELECT json_agg(pg_get_triggerdef(oid) ORDER BY tgname)
  FROM pg_catalog.pg_trigger WHERE tgrelid=c.oid AND NOT tgisinternal)) ORDER BY c.relname),'[]')
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p','f','m');
""")
    require(isinstance(rows, list) and 0 < len(rows) <= 256, "migration_catalog_budget")
    result = []
    for row in rows:
        # Exact controller ledger is separately bound by full schema and row proof.
        if row["name"] == LEDGER:
            continue
        identifier(row["name"])
        columns = tuple(
            CatalogColumn(
                c["name"],
                c["type"],
                c["nullable"],
                c["typmod"],
                digest(c["default"]) if c["default"] is not None else None,
                c["generated"],
                c["identity"],
                digest(c["collation"]) if c["collation"] is not None else None,
            )
            for c in row["columns"]
        )
        result.append(
            CatalogTable(
                "public",
                row["name"],
                columns,
                digest(row["constraints"]),
                digest(row["indexes"]),
                digest(row["policies"]),
                digest(row["triggers"]),
                row["persistence"],
                row["plain"],
                row["rls"],
                row["force"],
            )
        )
    require(
        session.json("SELECT to_json(NOT EXISTS(SELECT 1 FROM pg_catalog.pg_event_trigger));")
        is True,
        "migration_event_triggers_unsupported",
    )
    tables = tuple(result)
    catalog_digest(tables)  # Reject non-heap/custom types BEFORE any administrator row read.
    return tables


def row_proofs(session: Any, tables: tuple[CatalogTable, ...]) -> dict[str, Any]:
    catalog_digest(tables)
    columns = {t.name: [c.name for c in t.columns] for t in tables}
    ledger = verify_ledger(session)
    if ledger:
        columns[LEDGER] = ["name", "sha256", "applied_at"]
    result = {}
    for table, names in sorted(columns.items()):
        count = session.json(
            "SELECT count(*) FROM (SELECT 1 FROM public."
            + identifier(table, ledger=True)
            + " LIMIT "
            + str(MAX_ROWS + 1)
            + ") bounded;"
        )
        require(type(count) is int and 0 <= count <= MAX_ROWS, "migration_row_budget")
        selected = ",".join(identifier(name) for name in names)
        output = session.sql(
            "SELECT encode(sha256(convert_to(row_to_json(r)::text,'UTF8')),'hex') "
            "FROM (SELECT "
            + selected
            + " FROM public."
            + identifier(table, ledger=True)
            + ") r ORDER BY 1 LIMIT "
            + str(MAX_ROWS + 1)
            + ";"
        )
        hashes = output.splitlines()
        require(
            len(hashes) <= MAX_ROWS and all(re.fullmatch(rb"[0-9a-f]{64}", x) for x in hashes),
            "migration_row_budget",
        )
        result[table] = {
            "columns": names,
            "count": len(hashes),
            "sha256": hashlib.sha256(b"\n".join(hashes)).hexdigest(),
        }
    return result
