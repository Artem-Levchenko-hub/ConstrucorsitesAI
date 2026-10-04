import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from kubernetes.client.exceptions import ApiException

from tests.test_publication_trace import _response, _service, request
from yleum_orchestrator.services import k8s_publication as kp
from yleum_orchestrator.services.cell_publication import CellPublicationService


def failed_proxy(status=503, body=None):
    error = ApiException(status=status, reason="Service Unavailable")
    error.headers = {"Content-Type": "application/json", "Authorization": "PRIVATE_TOKEN"}
    error.body = body or (
        '{"reason":"ServiceUnavailable",'
        '"message":"PRIVATE customer no endpoints available for service"}'
    )
    calls = []

    def call(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    api = object.__new__(kp.KubernetesClusterApi)
    api._api_client = SimpleNamespace(call_api=call)
    return api, error, calls


def test_actual_proxy_preserves_same_exception_single_get_and_auth_with_safe_metadata():
    api, error, calls = failed_proxy()
    with pytest.raises(ApiException) as caught:
        api.proxy_get("app-" + str(UUID(int=1)), "boundary", 3000, "/api/omnia/health")
    assert caught.value is error
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (
        "/api/v1/namespaces/app-00000000-0000-0000-0000-000000000001/services/boundary:3000/proxy/api/omnia/health",
        "GET",
    )
    assert kwargs["auth_settings"] == ["BearerToken"]
    assert kwargs["_request_timeout"] == 20 and kwargs["_preload_content"] is False
    safe = getattr(error, "_omnia_boundary_http_failure", None)
    assert safe is not None and safe["status"] == 503
    assert safe["body_error_code"] == "ServiceUnavailable"
    assert "PRIVATE" not in json.dumps(safe)
    assert "Authorization" not in json.dumps(safe)


@pytest.mark.parametrize("value", ["no-such-code PRIVATE", {"code": "PRIVATE"}])
def test_unknown_reason_body_code_and_headers_never_escape(value):
    api, error, _ = failed_proxy(502, json.dumps({"error": value, "message": "PRIVATE"}))
    error.reason = "PRIVATE reason"
    error.headers["Content-Type"] = "PRIVATE"
    with pytest.raises(ApiException):
        api.proxy_get("app-" + str(UUID(int=1)), "boundary", 3000, "/api/omnia/health")
    safe = getattr(error, "_omnia_boundary_http_failure", None)
    assert safe is not None and safe["body_error_code"] is None
    assert "PRIVATE" not in json.dumps(safe)


def test_oversize_body_not_hashed_and_generic_exception_unchanged():
    api, error, _calls = failed_proxy(body="x" * 8193)
    with pytest.raises(ApiException):
        api.proxy_get("app-" + str(UUID(int=1)), "boundary", 3000, "/api/omnia/health")
    safe = getattr(error, "_omnia_boundary_http_failure", None)
    assert safe is not None and safe["body_budget_exceeded"] and safe["body_sha256"] is None
    ordinary = RuntimeError("PRIVATE")
    api._api_client.call_api = lambda *a, **k: (_ for _ in ()).throw(ordinary)
    with pytest.raises(RuntimeError) as caught:
        api.proxy_get("app-" + str(UUID(int=1)), "boundary", 3000, "/api/omnia/health")
    assert caught.value is ordinary and not hasattr(ordinary, "_omnia_boundary_http_failure")


def test_unrelated_proxy_path_does_not_get_boundary_metadata():
    api, error, calls = failed_proxy()
    with pytest.raises(ApiException):
        api.proxy_get("app-" + str(UUID(int=1)), "app", 3000, "/custom")
    assert not hasattr(error, "_omnia_boundary_http_failure") and len(calls) == 1


@pytest.mark.parametrize("rollback_fails", [False, True, "http"])
async def test_real_controller_persists_primary_http_before_rollback_and_restart(
    tmp_path, monkeypatch, rollback_fails
):
    value = request()
    run_id = str(UUID(int=99))
    service = _service(tmp_path, value, run_id)
    saved = service._read(value.project_id)
    saved["active_release"] = {
        "release_id": "old-release",
        "retained_volume": "owned-existing-data",
    }
    saved["data_seeded"] = True
    service._write(value.project_id, saved)
    api, _error, calls = failed_proxy()
    release = {"release_id": run_id, "prod_url": "https://qa.example.test", "image_id": "image"}
    runtime = SimpleNamespace(
        publish=lambda spec: api.proxy_get(
            "app-" + str(value.project_id), "boundary", 3000, "/api/omnia/health"
        )
    )
    service._kubernetes = lambda: SimpleNamespace(runtime=runtime)
    service._kubernetes_spec = AsyncMock(return_value=SimpleNamespace())

    async def rollback(old, request_):
        assert _response(service, value)["http_failure"]["status"] == 503
        assert old["retained_volume"] == "owned-existing-data"
        if rollback_fails == "http":
            rollback_api, _, _ = failed_proxy(401)
            rollback_api.proxy_get(
                "app-" + str(value.project_id), "boundary", 3000, "/api/omnia/health"
            )
        if rollback_fails:
            raise RuntimeError("PRIVATE rollback failure")

    service._rollback_kubernetes = AsyncMock(side_effect=rollback)

    async def prepare(*args):
        return release

    service._prepare = prepare
    service._activate = service._activate_kubernetes
    await service._execute(value, run_id)
    saved = service._read(value.project_id)
    row = saved["history"][-1]["response"]
    assert row["phase"] == "failed" and row["reason_code"] == "internal_error"
    assert row["http_failure"]["status"] == 503
    assert row["http_failure"]["operation"] == "boundary_health"
    assert (
        saved["active_release"]["retained_volume"] == "owned-existing-data"
        and saved["data_seeded"] is True
    )
    assert len(calls) == 1 and service._rollback_kubernetes.await_count == 1
    assert "PRIVATE" not in (tmp_path / str(value.project_id) / "publication.json").read_text()
    fresh = CellPublicationService(SimpleNamespace(), root=tmp_path)
    assert _response(fresh, value)["http_failure"] == row["http_failure"]
    # Private diagnostic is additive to the journal; existing public DTO unchanged.
    assert "http_failure" not in fresh.get(value.project_id).model_dump()


def test_success_response_contract_and_no_extra_request_are_unchanged():
    calls = []
    api = object.__new__(kp.KubernetesClusterApi)

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return (SimpleNamespace(status=200, data=b'{"status":"ok"}'), 200, {})

    api._api_client = SimpleNamespace(call_api=get)
    assert api.proxy_get("app-" + str(UUID(int=1)), "boundary", 3000, "/api/omnia/health") == (
        200,
        b'{"status":"ok"}',
    )
    assert len(calls) == 1
