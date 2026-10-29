import httpx
import pytest

from tests.test_max_runtime_probe import BOOTSTRAP, ORIGIN, PROJECT_ID, _install_transport
from yleum_api.services.max_runtime_probe import probe_max_cell_runtime
from yleum_api.services.orchestrator_client import ProjectCellPreviewSession


def preview_session():
    origin = f"https://cell-{PROJECT_ID.hex[:12]}-dev.preview.lead-generator.ru"
    return ProjectCellPreviewSession(
        PROJECT_ID,
        origin,
        BOOTSTRAP.replace(ORIGIN, origin),
        "2030-01-01T00:00:00Z",
    )


@pytest.mark.parametrize(
    "module_status,body,expected",
    [
        (200, {"records": [], "next_cursor": None}, True),
        (503, {"error": {"code": "unavailable"}}, False),
        (200, {"actions": []}, False),
        (200, {"records": [], "next_cursor": None, "private": "data"}, False),
    ],
)
async def test_acceptance_reads_real_registered_module_with_signed_preview_session(
    monkeypatch,
    module_status,
    body,
    expected,
):
    observed = []

    def handler(request):
        observed.append((request.method, request.url.path))
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(
                307,
                headers={"Location": "/", "Set-Cookie": "__Host-max_session=valid; Path=/; Secure"},
            )
        if request.url.path == "/__omnia/identity":
            if request.headers.get("cookie") != "__Host-max_session=valid":
                return httpx.Response(401)
            return httpx.Response(
                200, json={"project_id": str(PROJECT_ID), "user_id": "owner-preview", "epoch": 7}
            )
        if request.url.path == "/api/omnia/actions":
            return httpx.Response(200, json={"actions": []})
        if request.url.path == "/api/omnia/business/leads":
            assert request.headers["cookie"] == "__Host-max_session=valid"
            assert request.url.params["limit"] == "1"
            return httpx.Response(module_status, json=body)
        return httpx.Response(200, text="Product page")

    _install_transport(monkeypatch, handler)
    result = await probe_max_cell_runtime(
        preview_session(),
        portable_project_id=PROJECT_ID,
        expected_epoch=7,
        management_resources=("leads",),
    )
    assert result.ok is expected
    assert ("GET", "/api/omnia/business/leads") in observed
    assert all(method == "GET" for method, _ in observed)
    assert "private" not in result.detail and '"records"' not in result.detail


async def test_probe_never_accepts_manifest_supplied_paths_or_duplicate_resources(monkeypatch):
    _install_transport(monkeypatch, lambda _r: pytest.fail("invalid module must fail before HTTP"))
    preview = preview_session()
    for resources in (("private_diaries",), ("leads", "leads"), ("../actions",)):
        result = await probe_max_cell_runtime(preview, management_resources=resources)
        assert result.ok is False
