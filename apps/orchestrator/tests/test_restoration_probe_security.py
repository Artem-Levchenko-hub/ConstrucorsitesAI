"""Behavioral adversarial routes; all rows and actors are synthetic."""

from __future__ import annotations

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from tests.test_restoration_adaptation_health import ProbeApi, _prober, _request, _target
from yleum_orchestrator.core.cell_resources import CellResourceError


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["json", "escaped_json", "html"])
async def test_denial_status_cannot_hide_foreign_row(monkeypatch, encoding):
    api = ProbeApi()

    @api.app.middleware("http")
    async def leak(request: Request, call_next):
        response = await call_next(request)
        rows = list(api.rows.values())
        if response.status_code == 404 and rows:
            marker = str(rows[0]["marker"])
            if encoding == "html":
                return Response("<p>" + marker + "</p>", status_code=404)
            if encoding == "escaped_json":
                escaped = "".join(f"\\u{ord(c):04x}" for c in marker)
                return Response('{"error":"' + escaped + '"}', status_code=404)
            return JSONResponse({"error": {"row": rows[0]}}, status_code=404)
        return response

    prober = _prober(monkeypatch, api)
    request = _request()
    with pytest.raises(CellResourceError):
        await prober.verify_cross_owner_denial(request, _target(request))
    assert not api.database_rows


@pytest.mark.asyncio
@pytest.mark.parametrize("id_only", [False, True])
async def test_foreign_collection_cannot_return_fixture_even_without_marker(monkeypatch, id_only):
    api = ProbeApi()

    @api.app.middleware("http")
    async def leak(request: Request, call_next):
        response = await call_next(request)
        actor = api._actor(request)
        foreign = [r for r in api.rows.values() if r["ownerId"] != actor]
        if request.method == "GET" and request.url.path.endswith("restoration-probe") and foreign:
            return JSONResponse({"items": [{"id": foreign[0]["id"]}] if id_only else foreign})
        return response

    prober = _prober(monkeypatch, api)
    request = _request()
    with pytest.raises(CellResourceError):
        await prober.verify_cross_owner_denial(request, _target(request))
    assert not api.database_rows


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("location", ["ownerId", "max_user_id", "values"])
async def test_owner_spoof_cannot_transfer_fixture_even_with_honest_response(
    monkeypatch,
    method,
    location,
):
    api = ProbeApi()

    @api.app.middleware("http")
    async def transfer(request: Request, call_next):
        body = await request.json() if request.method in {"POST", "PATCH"} else {}
        response = await call_next(request)
        selected = (
            body.get("values", {}).get("max_user_id")
            if location == "values"
            else body.get(location)
        )
        if request.method == method and response.status_code in {200, 201} and selected:
            item_id = body.get("id") or request.url.path.rsplit("/", 1)[-1]
            api.rows[item_id]["ownerId"] = selected
            api.database_rows[item_id]["ownerId"] = selected
        return response

    prober = _prober(monkeypatch, api)
    request = _request()
    with pytest.raises(CellResourceError):
        await prober.verify_cross_owner_denial(request, _target(request))
    assert not api.database_rows


@pytest.mark.asyncio
async def test_safe_validation_echo_and_rejected_spoof_are_accepted(monkeypatch):
    api = ProbeApi()

    @api.app.middleware("http")
    async def validation(request: Request, call_next):
        if request.method in {"POST", "PATCH"}:
            body = await request.json()
            if any(k in body for k in ("ownerId", "max_user_id")) or "max_user_id" in body.get(
                "values", {}
            ):
                return JSONResponse({"error": "Unexpected field", "input": body}, status_code=422)
        return await call_next(request)

    prober = _prober(monkeypatch, api)
    request = _request()
    assert len(await prober.verify_cross_owner_denial(request, _target(request))) == 64
    assert not api.database_rows


@pytest.mark.asyncio
async def test_unauthenticated_denial_checks_real_protected_row_body(monkeypatch):
    api = ProbeApi()

    @api.app.middleware("http")
    async def leak(request: Request, call_next):
        response = await call_next(request)
        if response.status_code == 401:
            return JSONResponse(
                {"error": "Unauthorized", "rows": list(api.rows.values())}, status_code=401
            )
        return response

    prober = _prober(monkeypatch, api)
    request = _request()
    with pytest.raises(CellResourceError):
        await prober.verify_unauthenticated_denial(request, _target(request))
    assert not api.database_rows


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["id", "max_user_id", "probe_value"])
async def test_reference_app_rejects_reserved_values_before_any_sql(monkeypatch, column):
    import httpx

    from tests._probe_reference_app import ProbeReferenceApp
    from tests.test_restoration_adaptation_health import COOKIE, ORIGIN, PROBE, SECRET

    def no_sql(_statement):
        pytest.fail("reserved input reached SQL")

    request = _request()
    prober = _prober(monkeypatch, ProbeApi())
    context = prober._context(request, _target(request))
    app = ProbeReferenceApp(
        sql=no_sql,
        secret=SECRET,
        endpoint=PROBE.endpoint,
        contract_digest=PROBE.contract_digest,
        witness=PROBE.witnesses[0],
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            PROBE.endpoint,
            headers={"Cookie": f"{COOKIE}={context.owner_cookie}"},
            json={
                "id": "00000000-0000-0000-0000-000000000099",
                "entity": "orders",
                "marker": "synthetic",
                "phase": "created",
                "values": {column: "synthetic"},
            },
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_collection_may_echo_supplied_marker_without_leaking_foreign_item(monkeypatch):
    import json

    api = ProbeApi()

    @api.app.middleware("http")
    async def echo(request: Request, call_next):
        response = await call_next(request)
        if request.method == "GET" and request.url.path.endswith("restoration-probe"):
            raw = b"".join([chunk async for chunk in response.body_iterator])
            payload = json.loads(raw)
            payload["query"] = dict(request.query_params)
            return JSONResponse(payload, status_code=response.status_code)
        return response

    prober = _prober(monkeypatch, api)
    request = _request()
    assert len(await prober.verify_cross_owner_denial(request, _target(request))) == 64
    assert not api.database_rows


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["ownerId", "max_user_id", "values"])
async def test_foreign_patch_cannot_claim_victim_owner(monkeypatch, location):
    api = ProbeApi()

    @api.app.middleware("http")
    async def claimed_authority(request: Request, call_next):
        body = await request.json() if request.method == "PATCH" else {}
        row = api.rows.get(request.url.path.rsplit("/", 1)[-1])
        claim = (
            body.get("values", {}).get("max_user_id")
            if location == "values"
            else body.get(location)
        )
        insecure = bool(row and api._actor(request) != row["ownerId"] and claim == row["ownerId"])
        if insecure:
            api.insecure_methods.add("PATCH")
        try:
            return await call_next(request)
        finally:
            if insecure:
                api.insecure_methods.remove("PATCH")

    prober = _prober(monkeypatch, api)
    request = _request()
    with pytest.raises(CellResourceError):
        await prober.verify_cross_owner_denial(request, _target(request))
    assert not api.database_rows
