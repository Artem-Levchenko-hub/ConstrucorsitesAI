"""Phase 3 / stage B: several orchestrator hosts, one sticky binding per Project Cell."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from omnia_api.core.config import get_settings
from omnia_api.services import orchestrator_client, orchestrator_hosts, readiness
from omnia_api.services.orchestrator_hosts import (
    OrchestratorHostError,
    OrchestratorRegistry,
    forget_bindings,
    host_for_path,
    host_for_workspace,
    registry,
    remember_workspace_host,
)
from omnia_api.services.orchestrator_placement import pick_host

TWO_HOSTS = json.dumps(
    [
        {
            "name": "core",
            "url": "http://172.19.0.1:8003/",
            "preview_host_suffix": "dev.yleum.ru",
            "preview_resolver_rules": "MAP *.dev.yleum.ru 172.19.0.1",
        },
        {
            "name": "commerce",
            "url": "http://10.10.0.3:8003",
            "preview_host_suffix": "dev2.yleum.ru",
            "preview_resolver_rules": "MAP *.dev2.yleum.ru 10.10.0.3",
            "weight": 2,
        },
    ]
)


@pytest.fixture(autouse=True)
def _reset_bindings() -> Any:
    forget_bindings()
    orchestrator_hosts._registry_cache = None
    yield
    forget_bindings()
    orchestrator_hosts._registry_cache = None


def _configure(monkeypatch: pytest.MonkeyPatch, hosts: str, default: str = "core") -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "orchestrator_hosts", hosts)
    monkeypatch.setattr(settings, "default_orchestrator", default)
    monkeypatch.setattr(settings, "orchestrator_url", "http://localhost:8003")
    monkeypatch.setattr(settings, "project_cell_preview_host_suffix", "dev.yleum.ru")
    monkeypatch.setattr(settings, "gate_preview_resolver_rules", "MAP *.dev.yleum.ru 172.19.0.1")
    orchestrator_hosts._registry_cache = None


# ------------------------------------------------------------------ registry


def test_nothing_configured_is_the_single_legacy_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "")
    reg = registry()

    assert reg.single is True
    assert [host.name for host in reg.hosts] == ["core"]
    assert reg.get(None).url == "http://localhost:8003"
    assert reg.get("core").preview_host_suffix == "dev.yleum.ru"
    assert reg.resolver_rules() == "MAP *.dev.yleum.ru 172.19.0.1"
    with pytest.raises(OrchestratorHostError, match="unknown orchestrator host"):
        reg.get("commerce")


def test_two_hosts_parse_with_union_of_previews_and_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, TWO_HOSTS)
    reg = registry()

    assert reg.single is False
    assert reg.get("commerce").url == "http://10.10.0.3:8003"
    assert reg.get("core").url == "http://172.19.0.1:8003"  # trailing slash dropped
    assert reg.preview_suffixes() == {"dev.yleum.ru", "dev2.yleum.ru"}
    assert reg.resolver_rules() == "MAP *.dev.yleum.ru 172.19.0.1,MAP *.dev2.yleum.ru 10.10.0.3"
    assert [host.name for host in reg.enabled()] == ["core", "commerce"]
    assert registry() is reg  # cached per settings value


@pytest.mark.parametrize(
    ("hosts", "message"),
    [
        ("not json", "not valid JSON"),
        ("[]", "non-empty"),
        (
            json.dumps([{"name": "Bad Name", "url": "http://x", "preview_host_suffix": "a.ru"}]),
            "invalid",
        ),
        (json.dumps([{"name": "core", "url": "ftp://x", "preview_host_suffix": "a.ru"}]), "http"),
        (
            json.dumps([{"name": "core", "url": "http://x", "preview_host_suffix": "nodots"}]),
            "suffix",
        ),
        (
            json.dumps(
                [
                    {"name": "core", "url": "http://x", "preview_host_suffix": "a.ru"},
                    {"name": "core", "url": "http://y", "preview_host_suffix": "b.ru"},
                ]
            ),
            "twice",
        ),
        (
            json.dumps([{"name": "other", "url": "http://x", "preview_host_suffix": "a.ru"}]),
            "default",
        ),
        (
            json.dumps(
                [
                    {
                        "name": "core",
                        "url": "http://x",
                        "preview_host_suffix": "a.ru",
                        "enabled": False,
                    }
                ]
            ),
            "enabled",
        ),
    ],
)
def test_malformed_registry_is_refused(
    monkeypatch: pytest.MonkeyPatch, hosts: str, message: str
) -> None:
    _configure(monkeypatch, hosts)
    with pytest.raises(OrchestratorHostError, match=message):
        registry()


# ------------------------------------------------------------------ placement


def test_placement_is_least_loaded_by_weight_default_first_on_ties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, TWO_HOSTS)
    reg = registry()

    assert pick_host({}, reg) == "core"  # empty tie → default host
    assert pick_host({"core": 1}, reg) == "commerce"
    assert pick_host({"core": 1, "commerce": 1}, reg) == "commerce"  # 1/2 < 1/1
    assert pick_host({"core": 1, "commerce": 2}, reg) == "core"  # 1 == 1 → default
    assert pick_host({"core": 2, "commerce": 5}, reg) == "core"


def test_disabled_hosts_never_receive_new_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    items = json.loads(TWO_HOSTS)
    items[1]["enabled"] = False
    _configure(monkeypatch, json.dumps(items))

    assert pick_host({"core": 50}, registry()) == "core"


# ------------------------------------------------------------- path routing


@pytest.mark.asyncio
async def test_single_host_mode_never_looks_anything_up(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "")

    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no database lookup in single-host mode")

    monkeypatch.setattr(orchestrator_hosts, "_lookup", explode)
    assert await host_for_path(f"/internal/workspaces/{uuid4()}/agent/exec") is None
    assert await host_for_workspace(uuid4()) == "core"


@pytest.mark.asyncio
async def test_calls_follow_the_workspace_and_publications_follow_the_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, TWO_HOSTS)
    workspace = uuid4()
    project = uuid4()
    lookups: list[UUID] = []

    async def fake_lookup(column: Any, value: UUID) -> tuple[UUID, UUID, str] | None:
        lookups.append(value)
        if value in {workspace, project}:
            return workspace, project, "commerce"
        return None

    monkeypatch.setattr(orchestrator_hosts, "_lookup", fake_lookup)

    assert await host_for_path(f"/internal/workspaces/{workspace}/agent/exec") == "commerce"
    assert await host_for_path(f"/internal/projects/{project}/cell-deploy") == "commerce"
    assert await host_for_path(f"/internal/projects/{project}/deploy/history") == "commerce"
    # the workspace lookup primed both caches: the project needed no second query
    assert lookups == [workspace]
    # legacy slug-keyed and global routes stay on the default host
    assert await host_for_path(f"/internal/projects/{project}/agent/build") == "core"
    assert await host_for_path("/internal/deploy-targets/verify") == "core"
    # unknown identities resolve to the default host (an orchestrator that answers 404)
    assert await host_for_path(f"/internal/workspaces/{uuid4()}/resources") == "core"


@pytest.mark.asyncio
async def test_transport_targets_the_bound_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, TWO_HOSTS)
    settings = get_settings()
    monkeypatch.setattr(settings, "orchestrator_internal_token", None)
    workspace = uuid4()
    remember_workspace_host(workspace, None, "commerce")
    seen: list[str] = []

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"ok": "yes"}

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def request(self, method: str, url: str, **_kwargs: Any) -> FakeResponse:
            seen.append(url)
            return FakeResponse()

    from pydantic import SecretStr

    monkeypatch.setattr(settings, "orchestrator_internal_token", SecretStr("t"))
    monkeypatch.setattr(orchestrator_client.httpx, "AsyncClient", FakeClient)

    await orchestrator_client._request("GET", f"/internal/workspaces/{workspace}/resources")
    await orchestrator_client._request("GET", "/internal/health-ish", host="core")
    with pytest.raises(orchestrator_client.OrchestratorUnavailable, match="unknown orchestrator"):
        await orchestrator_client._request("GET", "/internal/x", host="nowhere")

    assert seen == [
        f"http://10.10.0.3:8003/internal/workspaces/{workspace}/resources",
        "http://172.19.0.1:8003/internal/health-ish",
    ]


# ------------------------------------------------------------------ previews


def test_preview_session_accepts_any_registered_host_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, TWO_HOSTS)
    workspace = uuid4()
    stem = f"cell-{workspace.hex[:12]}-dev"

    def session(suffix: str) -> dict[str, str]:
        return {
            "workspace_id": str(workspace),
            "state": "draft_running",
            "preview_url": f"https://{stem}.{suffix}/",
            "bootstrap_url": (
                f"https://{stem}.{suffix}/api/omnia/preview-session?expires=1&signature=" + "a" * 40
            ),
            "expires_at": "2026-09-23T10:00:00+00:00",
        }

    for suffix in ("dev.yleum.ru", "dev2.yleum.ru"):
        assert orchestrator_client.ProjectCellPreviewSession.from_json(session(suffix))
    with pytest.raises(orchestrator_client.OrchestratorUnavailable):
        orchestrator_client.ProjectCellPreviewSession.from_json(session("evil.example"))


def test_gate_browsers_resolve_every_hosts_preview_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnia_api.services.auth_session import preview_resolver_args

    _configure(monkeypatch, TWO_HOSTS)
    assert preview_resolver_args() == [
        "--host-resolver-rules=MAP *.dev.yleum.ru 172.19.0.1,MAP *.dev2.yleum.ru 10.10.0.3"
    ]
    _configure(monkeypatch, "")
    assert preview_resolver_args() == ["--host-resolver-rules=MAP *.dev.yleum.ru 172.19.0.1"]


# ------------------------------------------------------------------ readiness


@pytest.mark.asyncio
async def test_readiness_needs_every_host_and_reports_mixed_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, TWO_HOSTS)
    answers = {
        "http://172.19.0.1:8003": (True, "aaaaaaaa"),
        "http://10.10.0.3:8003": (True, "aaaaaaaa"),
    }

    async def probe(base_url: str) -> tuple[bool, str]:
        return answers[base_url]

    monkeypatch.setattr(readiness, "_probe_orchestrator", probe)
    assert await readiness._deploy_control_plane_ok() == (True, "aaaaaaaa")

    answers["http://10.10.0.3:8003"] = (True, "bbbbbbbb")
    assert await readiness._deploy_control_plane_ok() == (True, "mixed")

    answers["http://10.10.0.3:8003"] = (False, "unknown")
    assert await readiness._deploy_control_plane_ok() == (False, "mixed")


def test_registry_is_what_one_host_deployments_already_had(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback host is built from the three pre-existing settings, so a
    deployment that sets nothing new keeps its URL, suffix and resolver rule."""
    _configure(monkeypatch, "", default="core")
    reg: OrchestratorRegistry = registry()
    assert (reg.get("core").url, reg.get("core").preview_host_suffix) == (
        "http://localhost:8003",
        "dev.yleum.ru",
    )
