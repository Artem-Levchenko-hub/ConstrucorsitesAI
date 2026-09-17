"""In-memory fake of the production endpoints the generation canary drives.

The canary is an off-host probe, so its tests replace the network instead of
the code under test. One configurable fake keeps the release matrix, the
failure injection points and the recorded call order in a single place, which
is what the release-matrix and diagnostics suites both need.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

COMPONENTS = ("web", "api", "worker", "generation_worker", "orchestrator")
RELEASE_SHA = "a7c4fc22"
OTHER_RELEASE_SHA = "b18d5ee3"
PROJECT_ID = "10000000-0000-4000-8000-000000000001"
SEED_SNAPSHOT_ID = "20000000-0000-4000-8000-000000000001"
BUILD_RUN_ID = "30000000-0000-4000-8000-000000000001"
EDIT_RUN_ID = "30000000-0000-4000-8000-000000000002"
BUILD_SNAPSHOT_ID = "40000000-0000-4000-8000-000000000001"
EDIT_SNAPSHOT_ID = "40000000-0000-4000-8000-000000000002"
PREVIEW_HOST = "demo.preview.lead-generator.ru"
SIGNED_PREVIEW_URL = (
    f"https://{PREVIEW_HOST}/api/omnia/preview-session"
    "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)


def matrix(**overrides: str) -> dict[str, str]:
    return {component: RELEASE_SHA for component in COMPONENTS} | overrides


@dataclass
class FakeProduction:
    """Serve the canary's happy path until one injected failure changes it."""

    releases: dict[str, str] = field(default_factory=matrix)
    releases_after_edit: dict[str, str] | None = None
    health_status: int = 200
    health_body: str | None = None
    login_status: int = 200
    project_create_status: int = 201
    build_generation_status: str = "completed"
    edit_generation_status: str = "completed"
    runtime_state: str = "running"
    preview_status: int = 200
    delete_status: int = 204
    logout_status: int = 204
    calls: list[tuple[str, str]] = field(default_factory=list)
    _edit_started: bool = False
    _edit_prompted: bool = False
    _project_reads: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self, method: str) -> list[str]:
        return [path for called_method, path in self.calls if called_method == method]

    def _current_releases(self) -> dict[str, str]:
        if self._edit_prompted and self.releases_after_edit is not None:
            return self.releases_after_edit
        return self.releases

    def _health(self, service: str) -> httpx.Response:
        if self.health_body is not None:
            return httpx.Response(self.health_status, text=self.health_body)
        if self.health_status != 200:
            return httpx.Response(self.health_status, json={"status": "error"})
        releases = self._current_releases()
        payload: dict[str, object] = {
            "status": "ok",
            "service": service,
            "release_sha": releases[service],
        }
        if service == "api":
            payload["checks"] = {
                "database": "ok",
                "redis": "ok",
                "worker": "ok",
                "generation_worker": "ok",
                "deploy_control_plane": "ok",
                "preview_storage": "ok",
            }
            payload["dependencies"] = {
                "worker_release_sha": releases["worker"],
                "generation_worker_release_sha": releases["generation_worker"],
                "orchestrator_release_sha": releases["orchestrator"],
            }
        return httpx.Response(200, json=payload)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.calls.append((method, path))
        project_path = f"/api/projects/{PROJECT_ID}"

        if path == "/web-health":
            return self._health("web")
        if path == "/api/health":
            return self._health("api")
        if path == "/api/auth/login":
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"detail": "denied"})
            return httpx.Response(200, json={"id": "50000000-0000-4000-8000-000000000001"})
        if path == "/api/auth/logout":
            return httpx.Response(self.logout_status)
        if path == "/api/projects" and method == "POST":
            if self.project_create_status != 201:
                return httpx.Response(self.project_create_status, json={"detail": "denied"})
            return httpx.Response(
                201,
                json={"id": PROJECT_ID, "current_snapshot_id": SEED_SNAPSHOT_ID},
            )
        if path == f"{project_path}/prompt":
            prompt_mode = "edit" if self._edit_started else "build"
            if prompt_mode == "build":
                self._edit_started = True
                return httpx.Response(202, json={"run_id": BUILD_RUN_ID, "mode": "build"})
            self._edit_prompted = True
            return httpx.Response(202, json={"run_id": EDIT_RUN_ID, "mode": "edit"})
        if path == f"{project_path}/generation":
            building = self._project_reads == 0
            return httpx.Response(
                200,
                json={
                    "id": BUILD_RUN_ID if building else EDIT_RUN_ID,
                    "status": (
                        self.build_generation_status if building else self.edit_generation_status
                    ),
                    "response_mode": "build" if building else "edit",
                },
            )
        if path == project_path and method == "GET":
            self._project_reads += 1
            snapshot_id = BUILD_SNAPSHOT_ID if self._project_reads == 1 else EDIT_SNAPSHOT_ID
            return httpx.Response(200, json={"id": PROJECT_ID, "current_snapshot_id": snapshot_id})
        if path == project_path and method == "DELETE":
            return httpx.Response(self.delete_status)
        if path in {
            f"{project_path}/snapshots/{BUILD_SNAPSHOT_ID}",
            f"{project_path}/snapshots/{EDIT_SNAPSHOT_ID}",
        }:
            return httpx.Response(
                200,
                json={
                    "id": path.rsplit("/", 1)[-1],
                    "project_id": PROJECT_ID,
                    "files": {"src/app/page.tsx": "export default function Page() {}"},
                },
            )
        if path == f"{project_path}/runtime/start":
            return httpx.Response(200, json={"state": self.runtime_state})
        if path == f"{project_path}/max/preview-session":
            return httpx.Response(200, json={"url": SIGNED_PREVIEW_URL})
        if path == "/api/omnia/preview-session":
            return httpx.Response(307, headers={"location": "/"})
        if path == "/":
            return httpx.Response(self.preview_status, text="<!doctype html>")
        return httpx.Response(404, json={"detail": json.dumps({"path": path})})


def canary_env(monkeypatch, **overrides: str) -> None:
    """Configure the per-component release expectations the canary requires."""

    environment = {
        "PRODUCTION_CANARY_EMAIL": "canary@example.com",
        "PRODUCTION_CANARY_PASSWORD": "secret-password",
        "PRODUCTION_EXPECTED_WEB_RELEASE_SHA": RELEASE_SHA,
        "PRODUCTION_EXPECTED_API_RELEASE_SHA": RELEASE_SHA,
        "PRODUCTION_EXPECTED_WORKER_RELEASE_SHA": RELEASE_SHA,
        "PRODUCTION_EXPECTED_ORCHESTRATOR_RELEASE_SHA": RELEASE_SHA,
    }
    environment.update(overrides)
    monkeypatch.delenv("PRODUCTION_EXPECTED_RELEASE_SHA", raising=False)
    monkeypatch.delenv("PRODUCTION_EXPECTED_GENERATION_WORKER_RELEASE_SHA", raising=False)
    for name, value in environment.items():
        if value == "":
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
