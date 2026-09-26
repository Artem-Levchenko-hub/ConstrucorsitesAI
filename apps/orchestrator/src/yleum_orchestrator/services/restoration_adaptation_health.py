"""Fail-closed target-owned HTTP health proof for adaptive activation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import httpx

from yleum_orchestrator.core.cell_resources import CellResourceError, CellResourceNames
from yleum_orchestrator.schemas.restoration_adaptation_activation import ActivationBusinessWitness
from yleum_orchestrator.services import nginx_writer
from yleum_orchestrator.services.restoration_adaptation_activation_effects import (
    ActivationHealthTarget,
    live_database_volume_identity_digest,
)
from yleum_orchestrator.services.restoration_binding import canonical_digest
from yleum_orchestrator.services.restoration_database import admin_sql
from yleum_orchestrator.services.versioning.inventory import quote_ident

_SESSION_COOKIE = "__Host-max_session"
_SESSION_TTL_SECONDS = 120
_REQUEST_TIMEOUT_SECONDS = 10.0
_PROBE_SUITE_TIMEOUT_SECONDS = 45.0
_PROBE_CLEANUP_TIMEOUT_SECONDS = 10.0
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
HttpClientFactory = Callable[..., httpx.AsyncClient]
DatabaseWitnessReader = Callable[
    [Any, ActivationBusinessWitness, str],
    Awaitable[Mapping[str, object] | None],
]


class ProbeRehearsalFailure(CellResourceError):
    """Репетиция проверки упала на конкретной ноге, и нога названа.

    Внутри ног причина стирается намеренно (`raise ... from None`), чтобы наружу
    не утекли значения из чужой базы. Но сама нога — не данные, а имя шага, и
    без неё отказ нечитаем: и оператор, и агент починки получали одно слово на
    шесть разных бед.
    """

    def __init__(self, message: str, *, leg: str) -> None:
        super().__init__(message)
        self.leg = leg


# Нога репетиции → код причины для отчёта. Список закрытый: в отказ попадает
# имя шага, а не текст, поэтому утечь из базы здесь нечему.
REHEARSAL_REASON_BY_LEG = {
    "service_readiness": "probe_readiness_failed",
    "signed_owner_read": "probe_owner_read_failed",
    "signed_owner_mutation": "probe_owner_mutation_failed",
    "signed_owner_reload": "probe_owner_reload_failed",
    "cross_owner_denial": "probe_cross_owner_denial_failed",
    "unauthenticated_denial": "probe_unauthenticated_denial_failed",
}


class _ProbeRequest(Protocol):
    @property
    def operation_id(self) -> UUID: ...

    @property
    def generation_run_id(self) -> UUID: ...

    @property
    def project_id(self) -> UUID: ...

    @property
    def owner_id(self) -> UUID: ...

    @property
    def source_workspace_id(self) -> UUID: ...

    @property
    def target_fencing_epoch(self) -> int: ...

    @property
    def live_database_volume(self) -> str: ...

    @property
    def live_database_identity_digest(self) -> str: ...

    @property
    def business_probe(self) -> Any: ...

    @property
    def activation_id(self) -> UUID: ...

    @property
    def activation_digest(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _RehearsalRequest:
    operation_id: UUID
    generation_run_id: UUID
    project_id: UUID
    owner_id: UUID
    source_workspace_id: UUID
    target_fencing_epoch: int
    live_database_volume: str
    live_database_identity_digest: str
    business_probe: Any
    activation_id: UUID
    activation_digest: str


class _BoundedHttpClient:
    def __init__(
        self,
        factory: HttpClientFactory,
        *,
        origin: str,
        max_bytes: int,
        deadline: float,
    ) -> None:
        self._client = factory(
            base_url=origin,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        )
        self._max_bytes = max_bytes
        self._deadline = deadline

    async def __aenter__(self) -> _BoundedHttpClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc_value, traceback)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        remaining = self._deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise CellResourceError("activation health probe deadline exceeded")
        try:
            async with asyncio.timeout(remaining):
                async with self._client.stream(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json,
                ) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_bytes:
                            raise CellResourceError("activation health response is too large")
                    return httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=bytes(body),
                        request=response.request,
                    )
        except TimeoutError:
            raise CellResourceError("activation health probe deadline exceeded") from None

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return await self.request("GET", url, params=params, headers=headers)

    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        return await self.request("POST", url, headers=headers, json=json)

    async def patch(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        return await self.request("PATCH", url, headers=headers, json=json)

    async def delete(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return await self.request("DELETE", url, headers=headers)


@dataclass(frozen=True, slots=True)
class _ProbeContext:
    origin: str
    backend: Any = field(repr=False)
    owner_id: str
    owner_cookie: str = field(repr=False)
    cross_owner_id: str = field(repr=False)
    cross_owner_cookie: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _PendingReload:
    witness: ActivationBusinessWitness
    item_id: str
    marker: str
    phase: str


class DockerRestorationAdaptationHealthProber:
    """Prove adapted business routes against the unchanged live database."""

    def __init__(
        self,
        *,
        code_engine: Any,
        client_factory: HttpClientFactory = httpx.AsyncClient,
        database_reader: DatabaseWitnessReader | None = None,
    ) -> None:
        self._code_engine = code_engine
        self._client_factory = client_factory
        self._database_reader = database_reader or self._read_database_item
        self._pending_reloads: dict[UUID, tuple[_PendingReload, ...]] = {}
        self._context_overrides: dict[UUID, _ProbeContext] = {}
        self._suite_deadlines: dict[UUID, float] = {}

    async def rehearse_candidate(
        self,
        *,
        operation_id: UUID,
        generation_run_id: UUID,
        project_id: UUID,
        owner_id: UUID,
        activation_id: UUID,
        candidate_workspace_id: UUID,
        candidate_fencing_epoch: int,
        business_probe: Any,
        manager: Any,
        state: Any,
        backend: Any,
        candidate_source_manifest_digest: str,
    ) -> str:
        """Run the production proof against the isolated candidate and its DB copy."""

        database_volume = backend.project_postgres_volume
        database_identity = live_database_volume_identity_digest(
            backend, expected_volume=database_volume
        )
        binding = {
            "operation_id": str(operation_id),
            "generation_run_id": str(generation_run_id),
            "project_id": str(project_id),
            "owner_id": str(owner_id),
            "candidate_workspace_id": str(candidate_workspace_id),
            "candidate_fencing_epoch": candidate_fencing_epoch,
            "database_identity_digest": database_identity,
            "probe_contract_digest": business_probe.contract_digest,
            "candidate_source_manifest_digest": candidate_source_manifest_digest,
        }
        request = _RehearsalRequest(
            operation_id=operation_id,
            generation_run_id=generation_run_id,
            project_id=project_id,
            owner_id=owner_id,
            source_workspace_id=candidate_workspace_id,
            target_fencing_epoch=candidate_fencing_epoch,
            live_database_volume=database_volume,
            live_database_identity_digest=database_identity,
            business_probe=business_probe,
            activation_id=activation_id,
            activation_digest=canonical_digest(binding),
        )
        target = ActivationHealthTarget(
            workspace_id=candidate_workspace_id,
            project_id=project_id,
            owner_id=owner_id,
            fencing_epoch=candidate_fencing_epoch,
            code_volume=backend.workspace_volume,
            database_volume=database_volume,
            database_identity_digest=database_identity,
            business_probe=business_probe,
        )
        context = self._candidate_context(request, target, manager, state, backend)
        self._context_overrides[activation_id] = context
        self._suite_deadlines[activation_id] = (
            asyncio.get_running_loop().time() + _PROBE_SUITE_TIMEOUT_SECONDS
        )
        legs = (
            ("service_readiness", self.verify_service_readiness),
            ("signed_owner_read", self.verify_signed_owner_read),
            ("signed_owner_mutation", self.verify_signed_owner_create_update_delete),
            ("signed_owner_reload", self.verify_signed_owner_reload),
            ("cross_owner_denial", self.verify_cross_owner_denial),
            ("unauthenticated_denial", self.verify_unauthenticated_denial),
        )
        try:
            evidence = []
            for leg, verify in legs:
                try:
                    evidence.append(await verify(request, target))
                except CellResourceError as exc:
                    # Имя шага доживает до отчёта; текст остаётся во внутреннем
                    # исключении и наружу по-прежнему не идёт.
                    raise ProbeRehearsalFailure(str(exc), leg=leg) from exc
            return canonical_digest({"binding": binding, "evidence": evidence})
        finally:
            self._context_overrides.pop(activation_id, None)
            self._suite_deadlines.pop(activation_id, None)

    async def verify_service_readiness(
        self,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
    ) -> str:
        # Readiness is the first check of every activation health attempt.
        # Replace any abandoned attempt's deadline so an in-process retry has
        # the same bounded budget as the first try.
        self._suite_deadlines[request.activation_id] = (
            asyncio.get_running_loop().time() + _PROBE_SUITE_TIMEOUT_SECONDS
        )
        context = self._context(request, target)
        try:
            async with self._client(context.origin, request, target) as client:
                response = await client.get("/api/omnia/health")
            if response.status_code != 200 or self._json(response, target) != {
                "status": "ok",
                "platform": "max-miniapp",
            }:
                raise CellResourceError("activation readiness probe failed")
            return self._evidence(request, target, "service_readiness", {"status": 200})
        except CellResourceError:
            raise
        except Exception:
            raise CellResourceError("activation readiness probe failed") from None

    async def verify_signed_owner_read(
        self,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
    ) -> str:
        context = self._context(request, target)
        observed: dict[str, int] = {}
        try:
            async with self._client(context.origin, request, target) as client:
                for witness in target.business_probe.witnesses:
                    response = await client.get(
                        target.business_probe.endpoint,
                        params={"entity": witness.entity, "limit": 1},
                        headers=self._headers(context.origin, context.owner_cookie),
                    )
                    items = self._require_items(
                        response,
                        target=target,
                        witness=witness,
                        owner_id=context.owner_id,
                    )
                    for item in items:
                        await self._require_database_item(context, witness, item)
                    observed[witness.entity] = len(items)
            return self._evidence(
                request,
                target,
                "signed_owner_read",
                {"status": 200, "entity_counts": observed},
            )
        except CellResourceError:
            raise
        except Exception:
            raise CellResourceError("signed owner read probe failed") from None

    async def verify_signed_owner_create_update_delete(
        self,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
    ) -> str:
        context = self._context(request, target)
        created: list[_PendingReload] = []
        pending: list[_PendingReload] = []
        self._pending_reloads.pop(request.activation_id, None)
        try:
            async with self._client(context.origin, request, target) as client:
                for witness in target.business_probe.witnesses:
                    marker = f"{request.activation_id.hex}:{witness.entity}"
                    mutation_id = self._fixture_id(request, witness, "mutation")
                    reload_id = self._fixture_id(request, witness, "reload")
                    await self._clear_marker_items(
                        client,
                        context=context,
                        target=target,
                        witness=witness,
                        marker=marker,
                        allowed_ids={mutation_id, reload_id},
                    )
                    created_item = await self._create_item(
                        client,
                        context=context,
                        target=target,
                        witness=witness,
                        item_id=mutation_id,
                        marker=marker,
                        phase="created",
                    )
                    item_id = self._item_id(created_item)
                    current = _PendingReload(witness, item_id, marker, "created")
                    created.append(current)
                    await self._require_database_item(
                        context, witness, created_item
                    )
                    updated = await client.patch(
                        self._item_path(target, witness, item_id),
                        json={"phase": "updated"},
                        headers=self._headers(context.origin, context.owner_cookie),
                    )
                    updated_item = self._require_item(
                        updated,
                        expected_status=200,
                        target=target,
                        witness=witness,
                        owner_id=context.owner_id,
                        item_id=item_id,
                        marker=marker,
                        phase="updated",
                    )
                    await self._require_database_item(
                        context,
                        witness,
                        updated_item,
                    )
                    reread = await client.get(
                        self._item_path(target, witness, item_id),
                        headers=self._headers(context.origin, context.owner_cookie),
                    )
                    reread_item = self._require_item(
                        reread,
                        expected_status=200,
                        target=target,
                        witness=witness,
                        owner_id=context.owner_id,
                        item_id=item_id,
                        marker=marker,
                        phase="updated",
                    )
                    await self._require_database_item(context, witness, reread_item)
                    await self._delete_item(
                        client,
                        context=context,
                        target=target,
                        entry=current,
                        allow_missing=False,
                    )
                    created.remove(current)
                    deleted = await client.get(
                        self._item_path(target, witness, item_id),
                        headers=self._headers(context.origin, context.owner_cookie),
                    )
                    if deleted.status_code != 404:
                        raise CellResourceError("signed owner delete was not persistent")
                    reload_item = await self._create_item(
                        client,
                        context=context,
                        target=target,
                        witness=witness,
                        item_id=reload_id,
                        marker=marker,
                        phase="reload",
                    )
                    reload_entry = _PendingReload(
                        witness,
                        self._item_id(reload_item),
                        marker,
                        "reload",
                    )
                    created.append(reload_entry)
                    pending.append(reload_entry)
                    await self._require_database_item(
                        context, witness, reload_item
                    )
            self._pending_reloads[request.activation_id] = tuple(pending)
            return self._evidence(
                request,
                target,
                "signed_owner_create_update_delete",
                {
                    "status": 200,
                    "entities": [item.witness.entity for item in pending],
                    "item_digests": [self._digest_text(item.item_id) for item in pending],
                },
            )
        except Exception:
            self._pending_reloads.pop(request.activation_id, None)
            await self._cleanup(request, context, target, created)
            raise CellResourceError("signed owner mutation probe failed") from None

    async def verify_signed_owner_reload(
        self,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
    ) -> str:
        context = self._context(request, target)
        pending = self._pending_reloads.pop(request.activation_id, None)
        if pending is None:
            raise CellResourceError("signed owner reload probe state is unavailable")
        try:
            async with self._client(context.origin, request, target) as client:
                for entry in pending:
                    response = await client.get(
                        self._item_path(target, entry.witness, entry.item_id),
                        headers=self._headers(context.origin, context.owner_cookie),
                    )
                    item = self._require_item(
                        response,
                        expected_status=200,
                        target=target,
                        witness=entry.witness,
                        owner_id=context.owner_id,
                        item_id=entry.item_id,
                        marker=entry.marker,
                        phase=entry.phase,
                    )
                    await self._require_database_item(context, entry.witness, item)
            return self._evidence(
                request,
                target,
                "signed_owner_reload",
                {"status": 200, "entities": [item.witness.entity for item in pending]},
            )
        except Exception:
            raise CellResourceError("signed owner reload probe failed") from None
        finally:
            await self._cleanup(request, context, target, list(pending))

    async def verify_cross_owner_denial(
        self,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
    ) -> str:
        context = self._context(request, target)
        created: list[_PendingReload] = []
        try:
            async with self._client(context.origin, request, target) as client:
                for witness in target.business_probe.witnesses:
                    marker = f"{request.activation_id.hex}:cross:{witness.entity}"
                    item_id = self._fixture_id(request, witness, "cross")
                    await self._clear_marker_items(
                        client,
                        context=context,
                        target=target,
                        witness=witness,
                        marker=marker,
                        allowed_ids={item_id},
                    )
                    entry = _PendingReload(witness, item_id, marker, "cross-owner")
                    # Register the deterministic effect before POST. If the
                    # response is lost after the server commits, finally can
                    # delete it, and a fresh process clears the same marker/id.
                    created.append(entry)
                    item = await self._create_item(
                        client,
                        context=context,
                        target=target,
                        witness=witness,
                        item_id=item_id,
                        marker=marker,
                        phase="cross-owner",
                    )
                    if self._item_id(item) != entry.item_id:
                        raise CellResourceError("target business probe response changed")
                    await self._require_database_item(context, witness, item)
                    denied = await client.get(
                        self._item_path(target, witness, entry.item_id),
                        headers=self._headers(context.origin, context.cross_owner_cookie),
                    )
                    if denied.status_code != 404:
                        raise CellResourceError("cross-owner business row was visible")
                    denial_headers = self._headers(
                        context.origin, context.cross_owner_cookie
                    )
                    mutation_responses = (
                        await client.post(
                            target.business_probe.endpoint,
                            json={
                                "id": entry.item_id,
                                "entity": witness.entity,
                                "marker": marker,
                                "phase": "cross-owner-write",
                                "values": witness.create_values,
                            },
                            headers=denial_headers,
                        ),
                        await client.patch(
                            self._item_path(target, witness, entry.item_id),
                            json={"phase": "cross-owner-write"},
                            headers=denial_headers,
                        ),
                        await client.delete(
                            self._item_path(target, witness, entry.item_id),
                            headers=denial_headers,
                        ),
                    )
                    if any(
                        response.status_code not in {403, 404, 409}
                        for response in mutation_responses
                    ):
                        raise CellResourceError("cross-owner business mutation was admitted")
                    await self._require_database_item(context, witness, item)
            return self._evidence(
                request,
                target,
                "cross_owner_denial",
                {
                    "status": 404,
                    "owner_digest": self._digest_text(context.cross_owner_id),
                    "entities": [item.witness.entity for item in created],
                },
            )
        except CellResourceError:
            raise
        except Exception:
            raise CellResourceError("cross-owner denial probe failed") from None
        finally:
            await self._cleanup(request, context, target, created)

    async def verify_unauthenticated_denial(
        self,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
    ) -> str:
        context = self._context(request, target)
        try:
            async with self._client(context.origin, request, target) as client:
                for witness in target.business_probe.witnesses:
                    params: dict[str, str | int] = {
                        "entity": witness.entity,
                        "limit": 1,
                    }
                    item_id = self._fixture_id(request, witness, "unauthenticated")
                    path = self._item_path(target, witness, item_id)
                    body = {
                        "id": item_id,
                        "entity": witness.entity,
                        "marker": f"{request.activation_id.hex}:unauth:{witness.entity}",
                        "phase": "unauthenticated",
                        "values": witness.create_values,
                    }
                    responses: list[httpx.Response] = []
                    for headers in (
                        None,
                        self._headers(context.origin, "malformed"),
                    ):
                        responses.extend(
                            [
                                await client.get(
                                    target.business_probe.endpoint,
                                    params=params,
                                    headers=headers,
                                ),
                                await client.post(
                                    target.business_probe.endpoint,
                                    json=body,
                                    headers=headers,
                                ),
                                await client.get(path, headers=headers),
                                await client.patch(
                                    path,
                                    json={"phase": "unauthenticated"},
                                    headers=headers,
                                ),
                                await client.delete(path, headers=headers),
                            ]
                        )
                    if any(response.status_code != 401 for response in responses):
                        raise CellResourceError(
                            "unauthenticated business access was not denied"
                        )
                    absent = await self._database_reader(
                        context.backend, witness, item_id
                    )
                    if absent is not None:
                        raise CellResourceError(
                            "unauthenticated business mutation reached the database"
                        )
            return self._evidence(
                request,
                target,
                "unauthenticated_denial",
                {"missing_status": 401, "malformed_status": 401},
            )
        except CellResourceError:
            raise
        except Exception:
            raise CellResourceError("unauthenticated denial probe failed") from None
        finally:
            self._suite_deadlines.pop(request.activation_id, None)

    def _context(
        self,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
    ) -> _ProbeContext:
        try:
            override = self._context_overrides.get(request.activation_id)
            if override is not None:
                return override
            if (
                target.workspace_id != request.source_workspace_id
                or target.project_id != request.project_id
                or target.owner_id != request.owner_id
                or target.fencing_epoch != request.target_fencing_epoch
                or target.database_volume != request.live_database_volume
                or target.database_identity_digest != request.live_database_identity_digest
                or target.business_probe != request.business_probe
            ):
                raise CellResourceError("activation health target identity changed")
            manager = self._code_engine.activation_manager(target.workspace_id)
            state = manager.state_store.load(target.workspace_id)
            if (
                state is None
                or state.workspace_id != target.workspace_id
                or state.project_id != target.project_id
                or state.owner_id != target.owner_id
                or state.fencing_epoch != target.fencing_epoch
                or manager.machine_runtime is None
            ):
                raise CellResourceError("activation health runtime identity changed")
            self._code_engine.activation_assert_target_controller(
                manager,
                state,
                epoch=target.fencing_epoch,
                generation_run_id=request.generation_run_id,
            )
            _machine, backend = manager.machine_runtime.parts(state)
            if (
                backend.workspace_volume != target.code_volume
                or backend.project_postgres_volume != target.database_volume
            ):
                raise CellResourceError("activation health runtime volumes changed")
            database_identity = live_database_volume_identity_digest(
                backend,
                expected_volume=target.database_volume,
            )
            if database_identity != target.database_identity_digest:
                raise CellResourceError("activation health database identity changed")
            names = CellResourceNames.for_workspace(target.workspace_id)
            slug = names.draft_preview_slug()
            origin = nginx_writer.dev_url(slug)
            expected_host = nginx_writer.dev_host(slug)
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or parsed.hostname != expected_host
                or parsed.netloc != expected_host
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise CellResourceError("activation preview origin is invalid")
            secret = manager.machine_runtime.secret(target.workspace_id)
            if not isinstance(secret, str) or len(secret) < 16:
                raise CellResourceError("activation session signer is unavailable")
            owner_id = str(target.owner_id)
            cross_owner_id = str(uuid5(request.activation_id, "activation-health-cross-owner"))
            if cross_owner_id == owner_id:
                raise CellResourceError("activation cross-owner identity is invalid")
            expires_at = int(time.time()) + _SESSION_TTL_SECONDS
            return _ProbeContext(
                origin=origin,
                backend=backend,
                owner_id=owner_id,
                owner_cookie=self._signed_cookie(
                    secret, owner_id, expires_at, f"probe_{request.activation_id.hex[:12]}"
                ),
                cross_owner_id=cross_owner_id,
                cross_owner_cookie=self._signed_cookie(
                    secret,
                    cross_owner_id,
                    expires_at,
                    f"probe_cross_{request.activation_id.hex[:12]}",
                ),
            )
        except CellResourceError:
            raise
        except Exception:
            raise CellResourceError("activation health runtime is unavailable") from None

    def _candidate_context(
        self,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
        manager: Any,
        state: Any,
        backend: Any,
    ) -> _ProbeContext:
        if (
            state is None
            or state.workspace_id != target.workspace_id
            or state.project_id != target.project_id
            or state.owner_id != target.owner_id
            or state.fencing_epoch != target.fencing_epoch
            or state.active_generation_run_id != request.generation_run_id
            or state.active_generation_fencing_epoch != target.fencing_epoch
            or manager.machine_runtime is None
        ):
            raise CellResourceError("candidate probe runtime identity changed")
        _machine, observed_backend = manager.machine_runtime.parts(state)
        if (
            # Сравниваем то, ЧЕМ объект является, а не тот ли это самый объект.
            # Живой parts() собирает новый объект доступа на каждый вызов, поэтому
            # сравнение через `is not` отвергало кандидата ВСЕГДА — и шесть шагов
            # репетиции не выполнялись на проде ни разу: наружу уходило общее
            # «репетиция провалилась». Воспроизведено на стенде 26.09.
            observed_backend.workspace_volume != backend.workspace_volume
            or observed_backend.project_postgres_volume != backend.project_postgres_volume
            or backend.workspace_volume != target.code_volume
            or backend.project_postgres_volume != target.database_volume
            or live_database_volume_identity_digest(
                backend, expected_volume=target.database_volume
            )
            != target.database_identity_digest
        ):
            raise CellResourceError("candidate probe volume identity changed")
        names = CellResourceNames.for_workspace(target.workspace_id)
        origin = nginx_writer.dev_url(names.draft_preview_slug())
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.hostname != nginx_writer.dev_host(names.draft_preview_slug())
            or parsed.netloc != parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise CellResourceError("candidate probe origin is invalid")
        secret = manager.machine_runtime.secret(target.workspace_id)
        if not isinstance(secret, str) or len(secret) < 16:
            raise CellResourceError("candidate probe signer is unavailable")
        owner_id = str(target.owner_id)
        cross_owner_id = str(uuid5(request.activation_id, "activation-health-cross-owner"))
        expires_at = int(time.time()) + _SESSION_TTL_SECONDS
        return _ProbeContext(
            origin=origin,
            backend=backend,
            owner_id=owner_id,
            owner_cookie=self._signed_cookie(
                secret, owner_id, expires_at, f"probe_{request.activation_id.hex[:12]}"
            ),
            cross_owner_id=cross_owner_id,
            cross_owner_cookie=self._signed_cookie(
                secret,
                cross_owner_id,
                expires_at,
                f"probe_cross_{request.activation_id.hex[:12]}",
            ),
        )

    def _client(
        self,
        origin: str,
        request: _ProbeRequest,
        target: ActivationHealthTarget,
        *,
        deadline: float | None = None,
    ) -> _BoundedHttpClient:
        if deadline is None:
            deadline = self._suite_deadlines.setdefault(
                request.activation_id,
                asyncio.get_running_loop().time() + _PROBE_SUITE_TIMEOUT_SECONDS,
            )
        return _BoundedHttpClient(
            self._client_factory,
            origin=origin,
            max_bytes=target.business_probe.max_payload_bytes,
            deadline=deadline,
        )

    @staticmethod
    def _signed_cookie(
        secret: str, actor_id: str, expires_at: int, username: str
    ) -> str:
        payload = {
            "id": actor_id,
            "firstName": "Activation",
            "lastName": "Health",
            "username": username,
            "languageCode": None,
            "photoUrl": None,
            "expiresAt": expires_at,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        signature = (
            base64.urlsafe_b64encode(
                hmac.digest(secret.encode(), encoded.encode("ascii"), "sha256")
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        return f"{encoded}.{signature}"

    @staticmethod
    def _headers(origin: str, cookie: str) -> dict[str, str]:
        return {"Cookie": f"{_SESSION_COOKIE}={cookie}", "Origin": origin}

    async def _create_item(
        self,
        client: _BoundedHttpClient,
        *,
        context: _ProbeContext,
        target: ActivationHealthTarget,
        witness: ActivationBusinessWitness,
        item_id: str,
        marker: str,
        phase: str,
    ) -> dict[str, object]:
        body = {
            "id": item_id,
            "entity": witness.entity,
            "marker": marker,
            "phase": phase,
            "values": witness.create_values,
        }
        if (
            len(json.dumps(body, separators=(",", ":")).encode())
            > target.business_probe.max_payload_bytes
        ):
            raise CellResourceError("activation business probe request is too large")
        response = await client.post(
            target.business_probe.endpoint,
            json=body,
            headers=self._headers(context.origin, context.owner_cookie),
        )
        item = self._require_item(
            response,
            expected_status=201,
            target=target,
            witness=witness,
            owner_id=context.owner_id,
            marker=marker,
            phase=phase,
            item_id=item_id,
        )
        return item

    async def _clear_marker_items(
        self,
        client: _BoundedHttpClient,
        *,
        context: _ProbeContext,
        target: ActivationHealthTarget,
        witness: ActivationBusinessWitness,
        marker: str,
        allowed_ids: set[str],
    ) -> None:
        response = await client.get(
            target.business_probe.endpoint,
            params={"entity": witness.entity, "marker": marker, "limit": 32},
            headers=self._headers(context.origin, context.owner_cookie),
        )
        items = self._require_items(
            response,
            target=target,
            witness=witness,
            owner_id=context.owner_id,
            require_complete=True,
        )
        entries: list[_PendingReload] = []
        for item in items:
            item_id = self._item_id(item)
            item_marker = item.get("marker")
            item_phase = item.get("phase")
            if (
                item_id not in allowed_ids
                or item_marker != marker
                or not isinstance(item_phase, str)
            ):
                raise CellResourceError("activation marker query escaped its scope")
            entries.append(
                _PendingReload(witness, item_id, item_marker, item_phase)
            )
        for entry in entries:
            await self._delete_item(
                client,
                context=context,
                target=target,
                entry=entry,
                allow_missing=False,
            )

    async def _delete_item(
        self,
        client: _BoundedHttpClient,
        *,
        context: _ProbeContext,
        target: ActivationHealthTarget,
        entry: _PendingReload,
        allow_missing: bool,
    ) -> None:
        response = await client.delete(
            self._item_path(target, entry.witness, entry.item_id),
            headers=self._headers(context.origin, context.owner_cookie),
        )
        if allow_missing and response.status_code == 404:
            await self._require_database_item(
                context,
                entry.witness,
                {
                    "id": entry.item_id,
                    "ownerId": context.owner_id,
                    "entity": entry.witness.entity,
                    "marker": entry.marker,
                    "phase": entry.phase,
                },
                present=False,
            )
            return
        payload = self._json(response, target)
        if (
            response.status_code != 200
            or payload.get("deleted") is not True
            or payload.get("id") != entry.item_id
            or payload.get("probeContractDigest")
            != target.business_probe.contract_digest
        ):
            raise CellResourceError("activation business probe cleanup was not confirmed")
        await self._require_database_item(
            context,
            entry.witness,
            {
                "id": entry.item_id,
                "ownerId": context.owner_id,
                "entity": entry.witness.entity,
                "marker": entry.marker,
                "phase": entry.phase,
            },
            present=False,
        )

    async def _cleanup(
        self,
        request: _ProbeRequest,
        context: _ProbeContext,
        target: ActivationHealthTarget,
        entries: list[_PendingReload],
    ) -> None:
        if not entries:
            return
        try:
            cleanup_deadline = (
                asyncio.get_running_loop().time() + _PROBE_CLEANUP_TIMEOUT_SECONDS
            )
            async with self._client(
                context.origin,
                request,
                target,
                deadline=cleanup_deadline,
            ) as client:
                for entry in entries:
                    await self._delete_item(
                        client,
                        context=context,
                        target=target,
                        entry=entry,
                        allow_missing=True,
                    )
        except Exception:
            raise CellResourceError("activation health cleanup failed") from None

    def _require_items(
        self,
        response: httpx.Response,
        *,
        target: ActivationHealthTarget,
        witness: ActivationBusinessWitness,
        owner_id: str,
        require_complete: bool = False,
    ) -> list[dict[str, object]]:
        payload = self._json(response, target)
        items = payload.get("items")
        if (
            response.status_code != 200
            or payload.get("probeContractDigest")
            != target.business_probe.contract_digest
            or not isinstance(items, list)
            or (require_complete and payload.get("complete") is not True)
        ):
            raise CellResourceError("signed owner business list is unavailable")
        result: list[dict[str, object]] = []
        for item in items:
            if (
                not isinstance(item, dict)
                or item.get("ownerId") != owner_id
                or item.get("entity") != witness.entity
            ):
                raise CellResourceError("signed owner business scope changed")
            self._item_id(item)
            result.append(item)
        return result

    def _require_item(
        self,
        response: httpx.Response,
        *,
        expected_status: int,
        target: ActivationHealthTarget,
        witness: ActivationBusinessWitness,
        owner_id: str,
        marker: str,
        phase: str,
        item_id: str | None = None,
    ) -> dict[str, object]:
        payload = self._json(response, target)
        item = payload.get("item")
        if (
            response.status_code != expected_status
            or payload.get("probeContractDigest")
            != target.business_probe.contract_digest
            or not isinstance(item, dict)
        ):
            raise CellResourceError("target business probe capability is unavailable")
        observed_id = self._item_id(item)
        if (
            (item_id is not None and observed_id != item_id)
            or item.get("ownerId") != owner_id
            or item.get("entity") != witness.entity
            or item.get("marker") != marker
            or item.get("phase") != phase
        ):
            raise CellResourceError("target business probe response changed")
        return item

    @staticmethod
    def _fixture_id(
        request: _ProbeRequest,
        witness: ActivationBusinessWitness,
        purpose: str,
    ) -> str:
        return str(
            uuid5(
                request.activation_id,
                f"business-probe:{witness.entity}:{purpose}",
            )
        )

    async def _require_database_item(
        self,
        context: _ProbeContext,
        witness: ActivationBusinessWitness,
        item: Mapping[str, object],
        *,
        present: bool = True,
    ) -> None:
        item_id = self._item_id(dict(item))
        observed = await self._database_reader(context.backend, witness, item_id)
        if not present:
            if observed is not None:
                raise CellResourceError("activation business row delete was not persistent")
            return
        marker = item.get("marker")
        phase = item.get("phase")
        if (
            not isinstance(marker, str)
            or not isinstance(phase, str)
            or observed
            != {
                "id": item_id,
                "ownerId": item.get("ownerId"),
                "value": f"{marker}:{phase}",
            }
        ):
            raise CellResourceError("target business response has no live database witness")

    @staticmethod
    async def _read_database_item(
        backend: Any,
        witness: ActivationBusinessWitness,
        item_id: str,
    ) -> Mapping[str, object] | None:
        parameters = json.dumps({"id": item_id}, separators=(",", ":")).encode().hex()
        table = quote_ident(witness.entity)
        id_column = quote_ident(witness.id_column)
        owner_column = quote_ident(witness.owner_column)
        value_column = quote_ident(witness.value_column)
        statement = f"""
SET statement_timeout = '5s';
SET default_transaction_read_only = on;
SELECT coalesce(json_agg(json_build_object(
  'id', target.{id_column}::text,
  'ownerId', target.{owner_column}::text,
  'value', target.{value_column}::text
)), '[]'::json)
FROM public.{table} AS target
CROSS JOIN json_to_record(
  convert_from(decode('{parameters}', 'hex'), 'UTF8')::json
) AS bound(id text)
WHERE target.{id_column}::text = bound.id;
"""
        raw = await asyncio.to_thread(admin_sql, backend, statement, max_bytes=8192)
        try:
            rows = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CellResourceError("activation database witness is invalid") from None
        if not isinstance(rows, list) or len(rows) > 1:
            raise CellResourceError("activation database witness is invalid")
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict) or set(row) != {"id", "ownerId", "value"}:
            raise CellResourceError("activation database witness is invalid")
        return row

    @staticmethod
    def _item_id(item: dict[str, object]) -> str:
        value = item.get("id")
        if not isinstance(value, str) or _ITEM_ID_RE.fullmatch(value) is None:
            raise CellResourceError("activation business item identity is invalid")
        try:
            canonical = str(UUID(value))
        except ValueError:
            raise CellResourceError("activation business item identity is invalid") from None
        if canonical != value.lower():
            raise CellResourceError("activation business item identity is invalid")
        return canonical

    @staticmethod
    def _item_path(
        target: ActivationHealthTarget,
        witness: ActivationBusinessWitness,
        item_id: str,
    ) -> str:
        canonical = DockerRestorationAdaptationHealthProber._item_id({"id": item_id})
        return f"{target.business_probe.endpoint}/{witness.entity}/{canonical}"

    @staticmethod
    def _json(
        response: httpx.Response, target: ActivationHealthTarget
    ) -> dict[str, Any]:
        if len(response.content) > target.business_probe.max_payload_bytes:
            raise CellResourceError("activation health response is too large")
        try:
            value = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CellResourceError("activation health response is invalid") from None
        if not isinstance(value, dict):
            raise CellResourceError("activation health response is invalid")
        return value

    @staticmethod
    def _digest_text(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _evidence(
        request: _ProbeRequest,
        target: ActivationHealthTarget,
        check: str,
        observed: dict[str, object],
    ) -> str:
        return canonical_digest(
            {
                "version": 2,
                "check": check,
                "activation_digest": request.activation_digest,
                "workspace_digest": hashlib.sha256(str(target.workspace_id).encode()).hexdigest(),
                "code_volume_digest": hashlib.sha256(target.code_volume.encode()).hexdigest(),
                "database_volume_digest": hashlib.sha256(
                    target.database_volume.encode()
                ).hexdigest(),
                "database_identity_digest": target.database_identity_digest,
                "business_probe_contract_digest": target.business_probe.contract_digest,
                "observed": observed,
            }
        )
