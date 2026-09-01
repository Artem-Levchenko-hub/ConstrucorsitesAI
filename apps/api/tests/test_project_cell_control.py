from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import ModuleType
from uuid import UUID, uuid4

import pytest

from omnia_api.models.user import User
from omnia_api.routers import messages
from omnia_api.services import project_cell_control
from omnia_api.services.project_cell_access import ProjectCellAccessDecision
from omnia_api.services.project_cell_control import (
    ProjectCellControlReadiness,
    inspect_project_cell_control,
)

_CONTROL_MODULE = "omnia_api.services.project_cell_control"
_ORCHESTRATOR_CLIENT_MODULE = "omnia_api.services.orchestrator_client"
_CONTROL_CALLS = frozenset(
    {"inspect_project_cell_control", "get_project_cell_capabilities"}
)


def _dotted_name(expression: ast.expr) -> str:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        prefix = _dotted_name(expression.value)
        return f"{prefix}.{expression.attr}" if prefix else expression.attr
    return ""


def _public_prompt_boundary_violations(tree: ast.AST) -> list[str]:
    violations: set[str] = set()
    forbidden_function_aliases = set(_CONTROL_CALLS)
    forbidden_module_aliases = {"project_cell_control"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _CONTROL_MODULE:
                    violations.add(f"import:{alias.name}@{node.lineno}")
                    forbidden_module_aliases.add(
                        alias.asname or alias.name.rsplit(".", 1)[-1]
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                qualified = f"{module}.{alias.name}" if module else alias.name
                bound_name = alias.asname or alias.name
                if module == _CONTROL_MODULE:
                    violations.add(f"import:{qualified}@{node.lineno}")
                    forbidden_function_aliases.add(bound_name)
                elif module == _ORCHESTRATOR_CLIENT_MODULE and (
                    alias.name == "get_project_cell_capabilities"
                ):
                    violations.add(f"import:{qualified}@{node.lineno}")
                    forbidden_function_aliases.add(bound_name)
                elif module == "omnia_api.services" and alias.name == "project_cell_control":
                    violations.add(f"import:{qualified}@{node.lineno}")
                    forbidden_module_aliases.add(bound_name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _dotted_name(node.func)
        parts = called.split(".")
        if (
            called in forbidden_function_aliases
            or (parts and parts[-1] in _CONTROL_CALLS)
            or (parts and parts[0] in forbidden_module_aliases)
        ):
            violations.add(f"call:{called}@{node.lineno}")

    return sorted(violations)


def _user() -> User:
    return User(
        id=uuid4(),
        email="owner@example.test",
        password_hash="unused",
        is_anon=False,
        status="active",
        email_verified_at=datetime.now(UTC),
    )


def _select_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        project_cell_control,
        "decide_project_cell_access",
        lambda _user: ProjectCellAccessDecision(
            enabled=True,
            provider="docker_owner_canary",
            reason="owner_canary",
        ),
    )


def _capability(
    requested_project_id: UUID,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_id": str(requested_project_id),
        "provider": "docker_owner_canary",
        "enabled": True,
        "ready": False,
        "state": "unsupported",
        "detail": "docker owner canary is unsupported in the foundation",
    }
    payload.update(overrides)
    return payload


async def test_control_inspection_skips_orchestrator_for_legacy_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        project_cell_control,
        "decide_project_cell_access",
        lambda _user: ProjectCellAccessDecision(
            enabled=False,
            provider="legacy",
            reason="feature_disabled",
        ),
    )

    async def forbidden_network_call(_project_id: UUID) -> dict[str, object]:
        raise AssertionError("legacy selection must return before orchestrator I/O")

    monkeypatch.setattr(
        project_cell_control,
        "get_project_cell_capabilities",
        forbidden_network_call,
    )

    readiness = await inspect_project_cell_control(_user(), uuid4())

    assert readiness == ProjectCellControlReadiness(
        selected=False,
        ready=False,
        provider="legacy",
        reason="feature_disabled",
    )


async def test_selected_owner_fails_closed_while_provider_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_owner(monkeypatch)
    project_id = uuid4()
    calls: list[UUID] = []

    async def capability(selected_project_id: UUID) -> dict[str, object]:
        calls.append(selected_project_id)
        return _capability(project_id)

    monkeypatch.setattr(project_cell_control, "get_project_cell_capabilities", capability)

    readiness = await inspect_project_cell_control(_user(), project_id)

    assert readiness == ProjectCellControlReadiness(
        selected=True,
        ready=False,
        provider="docker_owner_canary",
        reason="provider_unsupported",
    )
    assert calls == [project_id]


async def test_selected_owner_accepts_only_valid_future_ready_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_owner(monkeypatch)
    project_id = uuid4()

    async def capability(_project_id: UUID) -> dict[str, object]:
        return _capability(
            project_id,
            ready=True,
            state="ready",
            detail="workspace provider is ready",
        )

    monkeypatch.setattr(project_cell_control, "get_project_cell_capabilities", capability)

    readiness = await inspect_project_cell_control(_user(), project_id)

    assert readiness == ProjectCellControlReadiness(
        selected=True,
        ready=True,
        provider="docker_owner_canary",
        reason="ready",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "project_id": "00000000-0000-0000-0000-000000000005",
            "provider": "docker_owner_canary",
            "enabled": True,
            "ready": False,
            "state": "unsupported",
        },
        _capability(UUID("00000000-0000-0000-0000-000000000005"))
        | {"project_id": uuid4()},
        _capability(UUID("00000000-0000-0000-0000-000000000005"), provider=7),
        _capability(UUID("00000000-0000-0000-0000-000000000005"), enabled=1),
        _capability(UUID("00000000-0000-0000-0000-000000000005"), ready="false"),
        _capability(UUID("00000000-0000-0000-0000-000000000005"), state=None),
        _capability(UUID("00000000-0000-0000-0000-000000000005"), detail={}),
        _capability(
            UUID("00000000-0000-0000-0000-000000000005"),
            ready=True,
            state="unsupported",
        ),
        _capability(
            UUID("00000000-0000-0000-0000-000000000005"),
            ready=False,
            state="ready",
        ),
        _capability(
            UUID("00000000-0000-0000-0000-000000000005"),
            ready=False,
            state="starting",
        ),
    ],
    ids=[
        "missing-detail",
        "uuid-not-string",
        "provider-not-string",
        "enabled-not-bool",
        "ready-not-bool",
        "state-not-string",
        "detail-not-string",
        "ready-with-unsupported-state",
        "not-ready-with-ready-state",
        "unknown-state",
    ],
)
async def test_selected_owner_rejects_malformed_or_inconsistent_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    _select_owner(monkeypatch)
    project_id = UUID("00000000-0000-0000-0000-000000000005")

    async def capability(_project_id: UUID) -> dict[str, object]:
        return payload

    monkeypatch.setattr(project_cell_control, "get_project_cell_capabilities", capability)

    readiness = await inspect_project_cell_control(_user(), project_id)

    assert readiness == ProjectCellControlReadiness(
        selected=True,
        ready=False,
        provider="docker_owner_canary",
        reason="invalid_capability_response",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": "00000000-0000-0000-0000-000000000006"},
        {"provider": "disabled", "enabled": False, "state": "disabled"},
    ],
    ids=["project-id", "provider"],
)
async def test_selected_owner_rejects_mismatched_capability_identity(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    _select_owner(monkeypatch)
    project_id = UUID("00000000-0000-0000-0000-000000000005")

    async def capability(_project_id: UUID) -> dict[str, object]:
        return _capability(project_id, **overrides)

    monkeypatch.setattr(project_cell_control, "get_project_cell_capabilities", capability)

    readiness = await inspect_project_cell_control(_user(), project_id)

    assert readiness == ProjectCellControlReadiness(
        selected=True,
        ready=False,
        provider="docker_owner_canary",
        reason="capability_mismatch",
    )


async def test_selected_owner_fails_closed_on_client_exception_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_owner(monkeypatch)

    async def unavailable(_project_id: UUID) -> dict[str, object]:
        raise RuntimeError("dummy-sensitive-upstream-detail")

    monkeypatch.setattr(project_cell_control, "get_project_cell_capabilities", unavailable)

    readiness = await inspect_project_cell_control(_user(), uuid4())

    assert readiness == ProjectCellControlReadiness(
        selected=True,
        ready=False,
        provider="docker_owner_canary",
        reason="provider_unavailable",
    )
    assert "dummy-sensitive" not in readiness.reason


def test_control_readiness_is_immutable() -> None:
    readiness = ProjectCellControlReadiness(
        selected=True,
        ready=False,
        provider="docker_owner_canary",
        reason="provider_unsupported",
    )

    with pytest.raises(FrozenInstanceError):
        readiness.ready = True  # type: ignore[misc]


def test_control_coordinator_has_no_persistence_imports() -> None:
    forbidden_service_origins = (
        "omnia_api.services.project_cells",
        "omnia_api.models.project_cell",
        "omnia_api.models.generation_run",
        "sqlalchemy",
    )
    coordinator_origins = {
        value.__name__ if isinstance(value, ModuleType) else getattr(value, "__module__", "")
        for value in vars(project_cell_control).values()
    }
    assert all(
        not origin.startswith(forbidden)
        for origin in coordinator_origins
        for forbidden in forbidden_service_origins
    )


def test_public_prompt_ast_guard_detects_function_local_control_import_and_call() -> None:
    mutated_router = ast.parse(
        """
async def public_prompt(project_id):
    from omnia_api.services.orchestrator_client import (
        get_project_cell_capabilities as capability,
    )
    return await capability(project_id)
"""
    )

    violations = _public_prompt_boundary_violations(mutated_router)

    assert any(item.startswith("import:") for item in violations)
    assert any(item.startswith("call:") for item in violations)


def test_public_prompt_router_has_no_project_cell_control_import_or_call() -> None:
    router_tree = ast.parse(inspect.getsource(messages))

    assert _public_prompt_boundary_violations(router_tree) == []
