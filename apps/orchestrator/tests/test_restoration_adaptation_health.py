from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.schemas.restoration_adaptation_activation import (
    RestorationAdaptationActivationRequest,
)
from omnia_orchestrator.services.restoration_adaptation_activation_effects import (
    ActivationHealthTarget,
)
from omnia_orchestrator.services.restoration_adaptation_health import (
    DockerRestorationAdaptationHealthProber,
    _BoundedHttpClient,
)
from omnia_orchestrator.services.restoration_adaptation_probe import validate_probe_contract
from omnia_orchestrator.services.restoration_binding import canonical_digest

ORIGIN = "https://cell-000000000005-dev.preview.example.test"
SECRET = "health-probe-test-secret-that-must-stay-private"
COOKIE = "__Host-max_session"
MANIFEST = json.dumps(
    {
        "version": 1,
        "endpoint": "/api/orders/restoration-probe",
        "witnesses": [
            {
                "entity": "orders",
                "id_column": "id",
                "owner_column": "max_user_id",
                "value_column": "probe_value",
                "create_values": {},
            }
        ],
        "max_payload_bytes": 4096,
    },
    sort_keys=True,
    separators=(",", ":"),
)
DATA_CONTRACT = {
    "version": 1,
    "tables": [
        {
            "name": "orders",
            "columns": [
                {
                    "name": "id",
                    "type": "uuid",
                    "nullable": False,
                    "default": "gen_random_uuid()",
                },
                {"name": "max_user_id", "type": "text", "nullable": False},
                {"name": "probe_value", "type": "text", "nullable": False},
            ],
            "owner_column": "max_user_id",
            "primary_key": ["id"],
        }
    ],
}
PROBE = validate_probe_contract(
    {".omnia/restoration-probe.json": MANIFEST}, DATA_CONTRACT
)


def _database_identity() -> str:
    return canonical_digest(
        {
            "name": "live-db",
            "created_at": "fixed",
            "driver": "local",
            "scope": "local",
            "options": {},
            "labels": {"omnia.kind": "project-volume"},
        }
    )


def _request() -> RestorationAdaptationActivationRequest:
    value: dict[str, object] = {
        "operation_id": UUID(int=1),
        "generation_run_id": UUID(int=2),
        "project_id": UUID(int=3),
        "owner_id": UUID(int=4),
        "source_workspace_id": UUID(int=5),
        "expected_source_fencing_epoch": 11,
        "target_fencing_epoch": 12,
        "source_workspace_revision": "1" * 64,
        "source_code_volume": "source-code",
        "live_database_volume": "live-db",
        "live_database_identity_digest": _database_identity(),
        "candidate_workspace_id": UUID(int=6),
        "candidate_fencing_epoch": 1,
        "candidate_workspace_revision": "3" * 64,
        "proof_digest": "4" * 64,
        "candidate_artifact_digest": "5" * 64,
        "candidate_source_manifest_digest": "8" * 64,
        "candidate_code_digest": "6" * 64,
        "business_probe": PROBE,
        "probe_rehearsal_digest": "9" * 64,
        "probe_rehearsal_database_digest": "a" * 64,
        "planned_commit_sha": "a" * 40,
        "activation_id": UUID(int=7),
        "activation_digest": "7" * 64,
    }
    draft = RestorationAdaptationActivationRequest.model_construct(**value)
    value["activation_digest"] = hashlib.sha256(
        json.dumps(
            draft.activation_binding_payload(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return RestorationAdaptationActivationRequest.model_validate(value)


def _target(request: RestorationAdaptationActivationRequest) -> ActivationHealthTarget:
    return ActivationHealthTarget(
        workspace_id=request.source_workspace_id,
        project_id=request.project_id,
        owner_id=request.owner_id,
        fencing_epoch=request.target_fencing_epoch,
        code_volume=f"target-code-{request.activation_id.hex}",
        database_volume=request.live_database_volume,
        database_identity_digest=request.live_database_identity_digest,
        business_probe=request.business_probe,
    )


class ProbeApi:
    def __init__(
        self,
        *,
        persist_database: bool = True,
        ignore_marker: bool = False,
        insecure_methods: set[str] | None = None,
        max_value_length: int | None = None,
        allowed_phases: set[str] | None = None,
    ) -> None:
        self.app = FastAPI()
        self.rows: dict[str, dict[str, object]] = {}
        self.database_rows: dict[str, dict[str, object]] = {}
        self.paths: list[str] = []
        self.tamper_reload = False
        self.crash_after_post = False
        self.persist_database = persist_database
        self.ignore_marker = ignore_marker
        self.insecure_methods = insecure_methods or set()
        self.max_value_length = max_value_length
        self.allowed_phases = allowed_phases

        @self.app.middleware("http")
        async def record(request: Request, call_next):
            self.paths.append(request.url.path)
            response = await call_next(request)
            if self.crash_after_post and request.method == "POST":
                self.crash_after_post = False
                raise RuntimeError("simulated process loss after POST effect")
            return response

        @self.app.get("/api/omnia/health")
        async def health():
            return {"status": "ok", "platform": "max-miniapp"}

        @self.app.get("/api/orders/restoration-probe")
        async def list_rows(
            request: Request,
            entity: str,
            limit: int = 1,
            marker: str | None = None,
        ):
            actor = self._actor(request)
            if actor is None:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            rows = [
                dict(row)
                for row in self.rows.values()
                if row["ownerId"] == actor
                and row["entity"] == entity
                and (
                    self.ignore_marker
                    or marker is None
                    or row["marker"] == marker
                )
            ][:limit]
            return {
                "probeContractDigest": PROBE.contract_digest,
                "items": rows,
                "complete": True,
            }

        @self.app.post("/api/orders/restoration-probe", status_code=201)
        async def create(request: Request):
            actor = self._actor(request)
            if actor is None:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await request.json()
            stored_value = f"{body['marker']}:{body['phase']}"
            if self.max_value_length is not None and len(stored_value) > self.max_value_length:
                return JSONResponse({"error": "value too long"}, status_code=422)
            if self.allowed_phases is not None and body["phase"] not in self.allowed_phases:
                return JSONResponse({"error": "check constraint"}, status_code=422)
            item_id = body["id"]
            existing = self.rows.get(item_id)
            if existing is not None and (
                existing["ownerId"] == actor or "POST" not in self.insecure_methods
            ):
                return JSONResponse({"error": "Conflict"}, status_code=409)
            row = {
                "id": item_id,
                "ownerId": actor,
                "entity": body["entity"],
                "marker": body["marker"],
                "phase": body["phase"],
            }
            self.rows[item_id] = row
            if self.persist_database:
                self.database_rows[item_id] = {
                    "id": item_id,
                    "ownerId": actor,
                    "value": f"{body['marker']}:{body['phase']}",
                }
            return {"probeContractDigest": PROBE.contract_digest, "item": row}

        @self.app.get("/api/orders/restoration-probe/{entity}/{item_id}")
        async def get_item(request: Request, entity: str, item_id: str):
            actor = self._actor(request)
            if actor is None:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            row = self.rows.get(item_id)
            if row is None or row["entity"] != entity or (
                row["ownerId"] != actor and "GET" not in self.insecure_methods
            ):
                return JSONResponse({"error": "Not found"}, status_code=404)
            result = dict(row)
            if self.tamper_reload and result["phase"] == "reload":
                result["phase"] = "tampered"
            return {"probeContractDigest": PROBE.contract_digest, "item": result}

        @self.app.patch("/api/orders/restoration-probe/{entity}/{item_id}")
        async def patch_item(request: Request, entity: str, item_id: str):
            actor = self._actor(request)
            row = self.rows.get(item_id)
            if actor is None:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if row is None or row["entity"] != entity or (
                row["ownerId"] != actor and "PATCH" not in self.insecure_methods
            ):
                return JSONResponse({"error": "Not found"}, status_code=404)
            row["phase"] = (await request.json())["phase"]
            if self.allowed_phases is not None and row["phase"] not in self.allowed_phases:
                row["phase"] = "created"
                return JSONResponse({"error": "check constraint"}, status_code=422)
            if self.persist_database and item_id in self.database_rows:
                self.database_rows[item_id]["value"] = (
                    f"{row['marker']}:{row['phase']}"
                )
            return {"probeContractDigest": PROBE.contract_digest, "item": row}

        @self.app.delete("/api/orders/restoration-probe/{entity}/{item_id}")
        async def delete_item(request: Request, entity: str, item_id: str):
            actor = self._actor(request)
            row = self.rows.get(item_id)
            if actor is None:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if row is None or row["entity"] != entity or (
                row["ownerId"] != actor and "DELETE" not in self.insecure_methods
            ):
                return JSONResponse({"error": "Not found"}, status_code=404)
            del self.rows[item_id]
            if self.persist_database:
                self.database_rows.pop(item_id, None)
            return {
                "probeContractDigest": PROBE.contract_digest,
                "deleted": True,
                "id": item_id,
            }

    @staticmethod
    def _actor(request: Request) -> str | None:
        raw = request.cookies.get(COOKIE)
        if raw is None or "." not in raw:
            return None
        encoded, signature = raw.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.digest(SECRET.encode(), encoded.encode(), "sha256")
        ).rstrip(b"=").decode()
        if not hmac.compare_digest(signature, expected):
            return None
        padding = "=" * (-len(encoded) % 4)
        return json.loads(base64.urlsafe_b64decode(encoded + padding))["id"]


class FakeCodeEngine:
    def __init__(self, request: RestorationAdaptationActivationRequest) -> None:
        attrs = {
            "Name": request.live_database_volume,
            "CreatedAt": "fixed",
            "Driver": "local",
            "Scope": "local",
            "Options": {},
            "Labels": {"omnia.kind": "project-volume"},
        }
        volume = SimpleNamespace(attrs=attrs)
        backend = SimpleNamespace(
            workspace_volume=f"target-code-{request.activation_id.hex}",
            project_postgres_volume=request.live_database_volume,
            client=SimpleNamespace(volumes={request.live_database_volume: volume}),
            _lookup=lambda collection, name, _kind: collection.get(name),
        )
        state = SimpleNamespace(
            workspace_id=request.source_workspace_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            fencing_epoch=request.target_fencing_epoch,
            active_generation_run_id=request.generation_run_id,
            active_generation_fencing_epoch=request.target_fencing_epoch,
        )
        self.manager = SimpleNamespace(
            state_store=SimpleNamespace(load=lambda _workspace: state),
            machine_runtime=SimpleNamespace(
                parts=lambda _state: (object(), backend),
                secret=lambda _workspace: SECRET,
            ),
        )

    def activation_manager(self, _workspace_id):
        return self.manager

    @staticmethod
    def activation_assert_target_controller(*_args, **_kwargs):
        return None


def _prober(monkeypatch: pytest.MonkeyPatch, api: ProbeApi):
    request = _request()
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_adaptation_health.nginx_writer.dev_url",
        lambda _slug: ORIGIN,
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_adaptation_health.nginx_writer.dev_host",
        lambda _slug: "cell-000000000005-dev.preview.example.test",
    )

    def client_factory(**kwargs):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url=kwargs["base_url"],
            follow_redirects=kwargs["follow_redirects"],
        )

    async def database_reader(_backend, _witness, item_id):
        row = api.database_rows.get(item_id)
        return dict(row) if row is not None else None

    return DockerRestorationAdaptationHealthProber(
        code_engine=FakeCodeEngine(request),
        client_factory=client_factory,
        database_reader=database_reader,
    )


@pytest.mark.asyncio
async def test_health_prober_uses_target_business_route_for_full_live_db_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi()
    prober = _prober(monkeypatch, api)
    request = _request()
    target = _target(request)

    evidence = [
        await prober.verify_service_readiness(request, target),
        await prober.verify_signed_owner_read(request, target),
        await prober.verify_signed_owner_create_update_delete(request, target),
        await prober.verify_signed_owner_reload(request, target),
        await prober.verify_cross_owner_denial(request, target),
        await prober.verify_unauthenticated_denial(request, target),
    ]

    assert all(len(item) == 64 for item in evidence)
    assert not api.rows
    assert not api.database_rows
    assert "/api/orders/restoration-probe" in api.paths
    assert "/api/omnia/actions" not in api.paths


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, *, endless: bool = False) -> None:
        self.endless = endless
        self.closed = False

    async def __aiter__(self):
        if self.endless:
            while True:
                await asyncio.sleep(0)
                yield b"x"
        else:
            for _ in range(5):
                yield b"x" * 1024

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_bounded_http_client_aborts_oversized_chunked_response() -> None:
    stream = _ChunkStream()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream, request=request)
    )
    client = _BoundedHttpClient(
        lambda **kwargs: httpx.AsyncClient(transport=transport, base_url=kwargs["base_url"]),
        origin=ORIGIN,
        max_bytes=4096,
        deadline=asyncio.get_running_loop().time() + 1,
    )

    with pytest.raises(CellResourceError, match="too large"):
        async with client:
            await client.get("/stream")
    assert stream.closed is True


@pytest.mark.asyncio
async def test_bounded_http_client_stops_frequent_endless_chunks_at_suite_deadline() -> None:
    stream = _ChunkStream(endless=True)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream, request=request)
    )
    client = _BoundedHttpClient(
        lambda **kwargs: httpx.AsyncClient(transport=transport, base_url=kwargs["base_url"]),
        origin=ORIGIN,
        max_bytes=8192,
        deadline=asyncio.get_running_loop().time() + 0.01,
    )

    with pytest.raises(CellResourceError, match="deadline"):
        async with client:
            await client.get("/stream")
    assert stream.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "api",
    [
        ProbeApi(max_value_length=10),
        ProbeApi(allowed_phases={"created", "reload", "cross-owner"}),
    ],
    ids=["varchar-10", "check-enum"],
)
async def test_candidate_rehearsal_rejects_live_column_constraints(
    monkeypatch: pytest.MonkeyPatch, api: ProbeApi
) -> None:
    request = _request()
    prober = _prober(monkeypatch, api)
    manager = prober._code_engine.activation_manager(request.source_workspace_id)
    state = manager.state_store.load(request.source_workspace_id)
    _machine, backend = manager.machine_runtime.parts(state)

    with pytest.raises(CellResourceError):
        await prober.rehearse_candidate(
            operation_id=request.operation_id,
            generation_run_id=request.generation_run_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            activation_id=request.activation_id,
            candidate_workspace_id=request.source_workspace_id,
            candidate_fencing_epoch=request.target_fencing_epoch,
            business_probe=request.business_probe,
            manager=manager,
            state=state,
            backend=backend,
            candidate_source_manifest_digest=request.candidate_source_manifest_digest,
        )
    assert not api.database_rows


@pytest.mark.asyncio
async def test_health_prober_cleans_business_rows_when_reload_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi()
    prober = _prober(monkeypatch, api)
    request = _request()
    target = _target(request)
    await prober.verify_signed_owner_create_update_delete(request, target)
    api.tamper_reload = True

    with pytest.raises(CellResourceError, match="signed owner reload probe failed"):
        await prober.verify_signed_owner_reload(request, target)

    assert not api.rows
    assert not api.database_rows


@pytest.mark.asyncio
async def test_health_prober_retry_removes_prior_crash_marker_before_new_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi()
    request = _request()
    target = _target(request)
    first = _prober(monkeypatch, api)
    await first.verify_signed_owner_create_update_delete(request, target)
    assert len(api.rows) == 1

    restarted = _prober(monkeypatch, api)
    await restarted.verify_signed_owner_create_update_delete(request, target)

    assert len(api.rows) == 1
    await restarted.verify_signed_owner_reload(request, target)
    assert not api.rows
    assert not api.database_rows


@pytest.mark.asyncio
async def test_health_probe_retry_starts_a_fresh_suite_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi()
    prober = _prober(monkeypatch, api)
    request = _request()
    target = _target(request)
    await prober.verify_service_readiness(request, target)
    first_calls = len(api.paths)
    prober._suite_deadlines[request.activation_id] = (
        asyncio.get_running_loop().time() - 1
    )

    evidence = await prober.verify_service_readiness(request, target)

    assert len(evidence) == 64
    assert len(api.paths) == first_calls + 1


@pytest.mark.asyncio
async def test_cross_owner_probe_restart_cleans_uncertain_deterministic_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi()
    request = _request()
    target = _target(request)
    crashed = _prober(monkeypatch, api)
    api.crash_after_post = True

    async def process_was_lost(*_args, **_kwargs):
        return None

    crashed._cleanup = process_was_lost
    with pytest.raises(CellResourceError):
        await crashed.verify_cross_owner_denial(request, target)
    assert len(api.rows) == 1
    assert len(api.database_rows) == 1

    restarted = _prober(monkeypatch, api)
    evidence = await restarted.verify_cross_owner_denial(request, target)

    assert len(evidence) == 64
    assert not api.rows
    assert not api.database_rows


@pytest.mark.asyncio
async def test_health_prober_rejects_http_crud_without_live_database_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi(persist_database=False)
    prober = _prober(monkeypatch, api)
    request = _request()

    with pytest.raises(CellResourceError, match="mutation probe failed"):
        await prober.verify_signed_owner_create_update_delete(
            request, _target(request)
        )

    assert not api.rows
    assert not api.database_rows


@pytest.mark.asyncio
async def test_marker_cleanup_rejects_unfiltered_real_row_without_deleting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi(ignore_marker=True)
    prober = _prober(monkeypatch, api)
    request = _request()
    real_id = str(uuid4())
    real = {
        "id": real_id,
        "ownerId": str(request.owner_id),
        "entity": "orders",
        "marker": "real-customer-row",
        "phase": "active",
    }
    api.rows[real_id] = dict(real)
    api.database_rows[real_id] = {
        "id": real_id,
        "ownerId": str(request.owner_id),
        "value": "real-customer-row:active",
    }

    with pytest.raises(CellResourceError, match="mutation probe failed"):
        await prober.verify_signed_owner_create_update_delete(
            request, _target(request)
        )

    assert api.rows[real_id] == real
    assert api.database_rows[real_id]["value"] == "real-customer-row:active"


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
@pytest.mark.asyncio
async def test_health_prober_rejects_insecure_cross_owner_mutations(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    api = ProbeApi(insecure_methods={method})
    prober = _prober(monkeypatch, api)
    request = _request()

    with pytest.raises(CellResourceError):
        await prober.verify_cross_owner_denial(request, _target(request))

    assert not api.rows
    assert not api.database_rows


@pytest.mark.asyncio
async def test_health_prober_rejects_changed_live_database_identity_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi()
    prober = _prober(monkeypatch, api)
    request = _request()
    target = replace(_target(request), database_identity_digest="f" * 64)

    with pytest.raises(CellResourceError, match="target identity changed"):
        await prober.verify_signed_owner_read(request, target)

    assert not api.paths


@pytest.mark.asyncio
async def test_health_prober_rejects_origin_outside_expected_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = ProbeApi()
    prober = _prober(monkeypatch, api)
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_adaptation_health.nginx_writer.dev_url",
        lambda _slug: "https://attacker.example.test",
    )
    request = _request()

    with pytest.raises(CellResourceError, match="preview origin"):
        await prober.verify_service_readiness(request, _target(request))

    assert not api.paths
