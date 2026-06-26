"""Unit tests for runnable export (services/project_export.py).

Pure logic, exercised against a temp skeleton dir (no container, no real
templates). Proves: skeleton+generated merge with generated winning, ignored
dirs skipped, README added, and fail-soft when the skeleton is absent.
"""

from __future__ import annotations

from pathlib import Path

from omnia_api.services import project_export as pe


def _make_skeleton(root: Path, name: str) -> Path:
    d = root / name
    (d / "src").mkdir(parents=True)
    (d / "src" / "engine.ts").write_text("export const engine = 1;", encoding="utf-8")
    (d / "package.json").write_text('{"name":"skeleton"}', encoding="utf-8")
    # noise that must NOT be exported
    (d / "node_modules" / "dep").mkdir(parents=True)
    (d / "node_modules" / "dep" / "index.js").write_text("junk", encoding="utf-8")
    (d / ".next").mkdir()
    (d / ".next" / "build").write_text("artifact", encoding="utf-8")
    return d


def test_read_template_tree_skips_deps_and_reads_source(tmp_path: Path):
    _make_skeleton(tmp_path, "stk")
    tree = pe.read_template_tree(tmp_path / "stk")
    assert "package.json" in tree
    assert "src/engine.ts" in tree
    # node_modules / .next never exported
    assert not any(p.startswith("node_modules/") for p in tree)
    assert not any(p.startswith(".next/") for p in tree)


def test_read_template_tree_missing_dir_is_empty(tmp_path: Path):
    assert pe.read_template_tree(tmp_path / "nope") == {}


def test_build_runnable_export_overlays_generated_over_skeleton(tmp_path: Path):
    _make_skeleton(tmp_path, "stk")
    generated = {
        "src/app/page.tsx": "export default function P(){return null}",
        "package.json": '{"name":"my-real-app"}',  # must WIN over the skeleton's
    }
    out = pe.build_runnable_export("stk", generated, templates_root=tmp_path)
    # skeleton file present
    assert out["src/engine.ts"] == "export const engine = 1;"
    # generated page present
    assert "src/app/page.tsx" in out
    # generated wins the conflict
    assert out["package.json"] == '{"name":"my-real-app"}'
    # README added
    assert "README.omnia.md" in out


def test_build_runnable_export_failsoft_when_skeleton_absent(tmp_path: Path):
    """No skeleton on disk → return the generated files unchanged (no worse than
    the snapshot-only zip; no README forced onto a fallback)."""
    generated = {"index.html": "<h1>hi</h1>"}
    out = pe.build_runnable_export("does-not-exist", generated, templates_root=tmp_path)
    assert out == generated


def test_build_runnable_export_keeps_existing_readme(tmp_path: Path):
    _make_skeleton(tmp_path, "stk")
    generated = {"README.omnia.md": "my own readme"}
    out = pe.build_runnable_export("stk", generated, templates_root=tmp_path)
    assert out["README.omnia.md"] == "my own readme"  # setdefault must not clobber
