"""Stack-registration consistency guard.

Adding a container stack touches several places that silently drift apart: the
`Template` literal, the orchestrator-name map, and the actual scaffold directory
(plus a DB migration + web label map elsewhere). When they disagree, project
creation 500s or the orchestrator can't find an image. This test fails fast if
the api-side trio is inconsistent — it would have caught a `realtime` map entry
with no template dir, the exact integration tail of G001.
"""

from __future__ import annotations

import re
from pathlib import Path

from omnia_api.schemas.project import (
    _ORCHESTRATOR_TEMPLATE_BY_API,
    Template,
)

# typing.Literal stores its members on __args__.
_TEMPLATE_VALUES = set(Template.__args__)  # type: ignore[attr-defined]
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "orchestrator" / "templates"


def test_every_orchestrator_key_is_a_valid_template() -> None:
    for api_value in _ORCHESTRATOR_TEMPLATE_BY_API:
        assert api_value in _TEMPLATE_VALUES, (
            f"orchestrator map key {api_value!r} is not in the Template literal"
        )


def test_every_orchestrator_template_dir_exists() -> None:
    for api_value, dir_name in _ORCHESTRATOR_TEMPLATE_BY_API.items():
        path = _TEMPLATES_DIR / dir_name
        assert path.is_dir(), f"template {api_value!r} -> {dir_name!r} but {path} does not exist"
        assert (path / "Dockerfile.dev").is_file(), (
            f"template {dir_name!r} has no Dockerfile.dev — orchestrator can't build it"
        )


def test_realtime_stack_registered() -> None:
    # Regression guard for G001: the realtime stack must stay fully wired.
    assert "realtime" in _TEMPLATE_VALUES
    assert _ORCHESTRATOR_TEMPLATE_BY_API.get("realtime") == "nextjs-realtime"
    assert (_TEMPLATES_DIR / "nextjs-realtime" / "Dockerfile.dev").is_file()


def test_max_miniapp_stack_registered() -> None:
    assert "max_miniapp" in _TEMPLATE_VALUES
    assert _ORCHESTRATOR_TEMPLATE_BY_API.get("max_miniapp") == "max-miniapp-nextjs"
    assert (_TEMPLATES_DIR / "max-miniapp-nextjs" / "Dockerfile.dev").is_file()


def test_max_starter_docker_context_and_windows_entrypoint_are_safe() -> None:
    template_dir = _TEMPLATES_DIR / "max-miniapp-nextjs"
    dockerignore = (template_dir / ".dockerignore").read_text(encoding="utf-8")
    dockerfile = (template_dir / "Dockerfile.dev").read_text(encoding="utf-8")

    assert "node_modules" in dockerignore
    assert ".next" in dockerignore
    assert "sed -i 's/\\r$//' docker-entrypoint.sh" in dockerfile


def test_max_starter_exposes_the_managed_integration_contract() -> None:
    omnia_dir = _TEMPLATES_DIR / "max-miniapp-nextjs" / "src" / "lib" / "omnia"
    implementation = omnia_dir / "client.ts"
    public_client = omnia_dir / "integration-client.ts"

    assert implementation.is_file()
    assert public_client.is_file()
    assert 'export * from "./client"' in public_client.read_text(encoding="utf-8")
    implementation_source = implementation.read_text(encoding="utf-8")
    for export_name in ("createMaxAction", "getMaxActions", "requestOmniaAI"):
        assert re.search(
            rf"export\s+(?:async\s+)?function\s+{export_name}\s*\(",
            implementation_source,
        )


def test_max_preview_identity_is_neutral_until_max_profile_is_verified() -> None:
    provider = (
        _TEMPLATES_DIR / "max-miniapp-nextjs" / "src" / "components" / "MaxAppProvider.tsx"
    ).read_text(encoding="utf-8")

    assert 'firstName: ""' in provider
    assert "lastName: null" in provider
    assert 'firstName: "Пользователь"' not in provider
