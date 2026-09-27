"""Owner-read refusals retain a bounded cause, never response or exception text."""

from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
import pytest

from tests.test_probe_rehearsal_names_its_leg import _prove_fixture
from tests.test_restoration_adaptation_health import ProbeApi, _prober, _request, _target
from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.restoration_adaptation_health import (
    OwnerReadFailure,
    OwnerReadStage,
    ProbeRehearsalFailure,
)
from yleum_orchestrator.services.restoration_adaptation_workspace import (
    RestorationAdaptationWorkspaceService,
)

SECRET = "private-business-value-and-token-must-not-escape"


def _fixture(monkeypatch, case):
    request = _request()
    target = _target(request)
    prober = _prober(monkeypatch, ProbeApi())
    item = {
        "id": str(UUID(int=101)),
        "ownerId": str(request.owner_id),
        "entity": "orders",
        "marker": "synthetic",
        "phase": "read",
    }
    payload = {"probeContractDigest": target.business_probe.contract_digest, "items": [item]}
    if case == "contract_digest":
        payload["probeContractDigest"] = SECRET
    elif case == "items_shape":
        payload["items"] = SECRET
    elif case == "item_shape":
        payload["items"] = [SECRET]
    elif case == "owner_scope":
        item["ownerId"] = SECRET
    elif case == "entity_scope":
        item["entity"] = SECRET
    elif case == "item_id":
        item["id"] = SECRET
    elif case == "witness_shape":
        item.pop("marker")

    async def handler(req):
        if case == "transport":
            raise httpx.ConnectError(SECRET)
        if case == "timeout":
            raise httpx.ReadTimeout(SECRET)
        if case == "unknown":
            raise RuntimeError(SECRET)
        if case == "cancelled":
            raise asyncio.CancelledError(SECRET)
        if case == "elapsed_deadline":
            await asyncio.sleep(1)
        if case == "http_status":
            return httpx.Response(401, json={"secret": SECRET})
        if case == "json_decode":
            return httpx.Response(200, content=SECRET.encode())
        if case == "json_shape":
            return httpx.Response(200, json=[SECRET])
        if case == "response_size":
            return httpx.Response(200, content=(SECRET * 200).encode())
        return httpx.Response(200, json=payload)

    def client_factory(**kwargs):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url=kwargs["base_url"],
            follow_redirects=kwargs["follow_redirects"],
        )

    async def database_reader(*_args):
        if case == "db_query":
            raise RuntimeError(SECRET)
        if case == "db_timeout":
            raise TimeoutError(SECRET)
        if case == "db_missing":
            return None
        return {
            "id": str(UUID(int=101)),
            "ownerId": str(request.owner_id),
            "value": SECRET if case == "db_mismatch" else "synthetic:read",
        }

    prober._client_factory = client_factory
    prober._database_reader = database_reader
    if case == "deadline":
        prober._suite_deadlines[request.activation_id] = asyncio.get_running_loop().time() - 1
    if case == "elapsed_deadline":
        prober._suite_deadlines[request.activation_id] = asyncio.get_running_loop().time() + 0.01
    return prober, request, target


@pytest.mark.parametrize(
    ("case", "suffix"),
    [
        ("http_status", "http_status_401"),
        ("json_decode", "json_shape"),
        ("json_shape", "json_shape"),
        ("contract_digest", "contract_digest"),
        ("items_shape", "item_shape"),
        ("item_shape", "item_shape"),
        ("owner_scope", "owner_scope"),
        ("entity_scope", "entity_scope"),
        ("item_id", "item_id"),
        ("witness_shape", "item_shape"),
        ("db_query", "db_query"),
        ("db_timeout", "db_timeout"),
        ("db_missing", "db_missing"),
        ("db_mismatch", "db_mismatch"),
        ("transport", "transport"),
        ("timeout", "timeout"),
        ("deadline", "deadline"),
        ("elapsed_deadline", "deadline"),
        ("response_size", "response_size"),
        ("unknown", "unknown"),
    ],
)
async def test_owner_read_names_actual_failure_without_payload(monkeypatch, case, suffix):
    prober, request, target = _fixture(monkeypatch, case)
    with pytest.raises(CellResourceError) as caught:
        await prober.verify_signed_owner_read(request, target)
    assert getattr(caught.value, "reason_detail", None) == "owner_read_" + suffix
    assert SECRET not in str(caught.value)
    assert SECRET not in repr(caught.value)


async def test_successful_owner_read_keeps_its_database_witness(monkeypatch):
    prober, request, target = _fixture(monkeypatch, "valid")
    assert len(await prober.verify_signed_owner_read(request, target)) == 64


@pytest.mark.parametrize(
    ("case", "suffix"),
    [
        ("http_status", "http_status_401"),
        ("contract_digest", "contract_digest"),
        ("owner_scope", "owner_scope"),
        ("db_missing", "db_missing"),
        ("db_mismatch", "db_mismatch"),
        ("db_query", "db_query"),
        ("response_size", "response_size"),
        ("unknown", "unknown"),
    ],
)
async def test_typed_failure_survives_rehearsal_materialization_and_receipt(
    monkeypatch, case, suffix
):
    prober, request, target = _fixture(monkeypatch, case)
    context = prober._context(request, target)
    monkeypatch.setattr(prober, "_candidate_context", lambda *_args: context)

    async def ready(*_args):
        return "a" * 64

    monkeypatch.setattr(prober, "verify_service_readiness", ready)
    with pytest.raises(ProbeRehearsalFailure) as caught:
        await prober.rehearse_candidate(
            operation_id=request.operation_id,
            generation_run_id=request.generation_run_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            activation_id=request.activation_id,
            candidate_workspace_id=target.workspace_id,
            candidate_fencing_epoch=target.fencing_epoch,
            business_probe=target.business_probe,
            manager=object(),
            state=object(),
            backend=context.backend,
            candidate_source_manifest_digest="a" * 64,
        )
    assert caught.value.leg == "signed_owner_read"

    class Rehearser:
        async def rehearse_candidate(self, **_kwargs):
            raise caught.value

    engine, prepared, proof_request = _prove_fixture(monkeypatch, Rehearser())
    materialized = await engine.prove(prepared, proof_request)
    receipt = RestorationAdaptationWorkspaceService._proof_result(
        prepared, proof_request, materialized
    )
    assert receipt.reason_code == "probe_owner_read_failed"
    assert receipt.reason_detail == "owner_read_" + suffix
    assert SECRET not in receipt.model_dump_json()


async def test_untyped_failure_text_cannot_become_owner_read_detail(monkeypatch):
    class Rehearser:
        async def rehearse_candidate(self, **_kwargs):
            raise ProbeRehearsalFailure(SECRET, leg="signed_owner_read")

    engine, prepared, proof_request = _prove_fixture(monkeypatch, Rehearser())
    result = await engine.prove(prepared, proof_request)
    assert result.reason_code == "probe_owner_read_failed"
    assert result.reason_detail is None


@pytest.mark.parametrize("status", [True, 99, 600, SECRET, None])
def test_http_diagnostic_rejects_unobserved_or_invalid_status(status):
    with pytest.raises(ValueError, match="invalid owner read diagnostic"):
        OwnerReadFailure(OwnerReadStage.HTTP_STATUS, status=status)


def test_stage_is_closed_and_http_status_cannot_attach_to_other_stages():
    with pytest.raises(ValueError, match="invalid owner read diagnostic"):
        OwnerReadFailure(SECRET)
    with pytest.raises(ValueError, match="invalid owner read diagnostic"):
        OwnerReadFailure(OwnerReadStage.UNKNOWN, status=401)
    failure = ProbeRehearsalFailure(
        "unrelated leg", leg="signed_owner_mutation",
        owner_read_failure=OwnerReadFailure(OwnerReadStage.DB_MISMATCH),
    )
    assert failure.owner_read_failure is None


async def test_cancellation_is_not_converted_into_repairable_owner_read_failure(monkeypatch):
    prober, request, target = _fixture(monkeypatch, "cancelled")
    with pytest.raises(asyncio.CancelledError):
        await prober.verify_signed_owner_read(request, target)
