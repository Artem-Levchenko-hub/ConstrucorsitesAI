"""Stack-registration consistency guard.

Adding a container stack touches several places that silently drift apart: the
`Template` literal, the orchestrator-name map, and the actual scaffold directory
(plus a DB migration + web label map elsewhere). When they disagree, project
creation 500s or the orchestrator can't find an image. This test fails fast if
the api-side trio is inconsistent — it would have caught a `realtime` map entry
with no template dir, the exact integration tail of G001.
"""

from __future__ import annotations

from pathlib import Path

from omnia_api.schemas.project import (
    _ORCHESTRATOR_TEMPLATE_BY_API,
    Template,
)

# typing.Literal stores its members on __args__.
_TEMPLATE_VALUES = set(Template.__args__)  # type: ignore[attr-defined]
_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[2] / "orchestrator" / "templates"
)


def test_every_orchestrator_key_is_a_valid_template() -> None:
    for api_value in _ORCHESTRATOR_TEMPLATE_BY_API:
        assert api_value in _TEMPLATE_VALUES, (
            f"orchestrator map key {api_value!r} is not in the Template literal"
        )


def test_every_orchestrator_template_dir_exists() -> None:
    for api_value, dir_name in _ORCHESTRATOR_TEMPLATE_BY_API.items():
        path = _TEMPLATES_DIR / dir_name
        assert path.is_dir(), (
            f"template {api_value!r} -> {dir_name!r} but {path} does not exist"
        )
        assert (path / "Dockerfile.dev").is_file(), (
            f"template {dir_name!r} has no Dockerfile.dev — orchestrator can't build it"
        )


def test_realtime_stack_registered() -> None:
    # Regression guard for G001: the realtime stack must stay fully wired.
    assert "realtime" in _TEMPLATE_VALUES
    assert _ORCHESTRATOR_TEMPLATE_BY_API.get("realtime") == "nextjs-realtime"
    assert (_TEMPLATES_DIR / "nextjs-realtime" / "Dockerfile.dev").is_file()
