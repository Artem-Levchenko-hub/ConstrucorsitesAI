"""Bounded internal transport. Runtime success is evidence, never inferred from HTTP 200."""

from typing import Any, Protocol

from omnia_api.schemas.restoration import RuntimeRestoration
from omnia_api.services.orchestrator_client import _request


class RestorationRuntime(Protocol):
    async def prepare(self, request: dict[str, Any]) -> RuntimeRestoration: ...
    async def status(self, request: dict[str, Any]) -> RuntimeRestoration: ...
    async def apply(self, request: dict[str, Any]) -> RuntimeRestoration: ...
    async def cancel(self, request: dict[str, Any]) -> RuntimeRestoration: ...


class HttpRestorationRuntime:
    async def _call(self, action: str, request: dict[str, Any]) -> RuntimeRestoration:
        path = (
            f"/internal/workspaces/{request['workspace_id']}/code-restorations/"
            f"{request['operation_id']}"
        )
        identity_keys = ("operation_id", "workspace_id", "project_id", "owner_id")
        if action == "cancel":
            wire = {key: request[key] for key in identity_keys}
        elif action == "apply":
            wire = {
                key: value
                for key, value in request.items()
                if key not in {"files", "current_files"}
            }
        else:
            wire = dict(request)
            if action == "prepare":
                wire["files"] = [
                    {**item, "mode": format(item["mode"], "o")} for item in request["files"]
                ]
                wire["current_files"] = [
                    {**item, "mode": format(item["mode"], "o")} for item in request["current_files"]
                ]
        payload = await _request(
            "GET" if action == "status" else "POST",
            path if action == "status" else f"{path}/{action}",
            json=None if action == "status" else wire,
            params={key: request[key] for key in ("project_id", "owner_id")}
            if action == "status"
            else None,
            timeout=30.0 if action == "status" else 930.0,
        )
        return RuntimeRestoration.model_validate(payload)

    async def prepare(self, request: dict[str, Any]) -> RuntimeRestoration:
        return await self._call("prepare", request)

    async def status(self, request: dict[str, Any]) -> RuntimeRestoration:
        return await self._call("status", request)

    async def apply(self, request: dict[str, Any]) -> RuntimeRestoration:
        return await self._call("apply", request)

    async def cancel(self, request: dict[str, Any]) -> RuntimeRestoration:
        return await self._call("cancel", request)
