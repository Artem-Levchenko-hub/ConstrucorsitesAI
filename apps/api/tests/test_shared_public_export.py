import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from yleum_api.services import project_export

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "orchestrator/tests/fixtures/shared_public_git_golden.json"
    ).read_text()
)


SOURCE_MAPPING = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "orchestrator/tests/fixtures/shared_source_git_mapping.json"
    ).read_text()
)


@pytest.mark.parametrize("template", GOLDEN["templates"])
def test_real_template_export_keeps_standalone_public_assets_and_generated_overrides(template):
    selected = "public/omnia-inspector.js"
    files = project_export.build_runnable_export(template, {selected: ""})
    assert files[selected] == ""
    for path in ["public/omnia-brief-narration.js", "public/omnia-remix-cta.js"]:
        value = files[path]
        data = value.encode() if isinstance(value, str) else value
        assert (
            hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
            == (GOLDEN["templates"][template][path]["sha256"])
        )


def test_missing_shared_asset_is_not_silently_omitted(tmp_path, monkeypatch):
    shutil.copytree(project_export._TEMPLATES_DIR / "shared-public", tmp_path / "shared-public")
    template = tmp_path / "max-miniapp-nextjs"
    template.mkdir()
    (template / "package.json").write_text("{}")
    (tmp_path / "shared-public/omnia-inspector.js").unlink()
    monkeypatch.setattr(project_export, "_TEMPLATES_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="shared template asset unavailable"):
        project_export.build_runnable_export("max-miniapp-nextjs", {})


@pytest.mark.parametrize("name", ["max-miniapp-nextjs"])
def test_generic_complete_template_with_matching_name_keeps_its_own_files(tmp_path, name):
    template = tmp_path / name
    template.mkdir()
    (template / "package.json").write_text("{}")
    assert project_export.read_template_tree(template) == {"package.json": "{}"}


def test_actual_export_smoke_with_clean_mounted_templates_outside_checkout(tmp_path):
    """Run the CI entrypoint against isolated API source and Git-equivalent template bytes."""
    api = Path(__file__).resolve().parents[1]
    mounted = tmp_path / "orchestrator/templates"
    shared = project_export._TEMPLATES_DIR / "shared-public"
    shutil.copytree(shared, mounted / "shared-public")
    for asset in (mounted / "shared-public").rglob("*"):
        if not asset.is_file():
            continue
        asset.write_bytes(asset.read_bytes().replace(b"\r\n", b"\n"))
    for template, expected in GOLDEN["templates"].items():
        for relative in expected:
            shared_public = relative in {
                "public/omnia-inspector.js",
                "public/omnia-brief-narration.js",
                "public/omnia-remix-cta.js",
            }
            shared_source = template in SOURCE_MAPPING.get(relative, {}).get("templates", [])
            if shared_public or shared_source:
                continue  # The mounted source is sparse; the actual API reader must expand it.
            source = project_export._TEMPLATES_DIR / template / relative
            target = mounted / template / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
    # Match the image's /app/src plus /orchestrator/templates relationship, with no
    # installed orchestrator or checkout on sys.path. This uses the real API reader.
    source_root = tmp_path / "app/src"
    service = source_root / "yleum_api/services/project_export.py"
    service.parent.mkdir(parents=True)
    shutil.copy2(Path(project_export.__file__), service)
    (service.parent / "__init__.py").touch()
    (service.parent.parent / "__init__.py").touch()
    script = tmp_path / "verify.py"
    shutil.copy2(api / "scripts/verify_shared_public_export.py", script)
    fixtures = api.parent / "orchestrator/tests/fixtures"
    env = os.environ | {"PYTHONPATH": str(source_root), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(fixtures / "shared_public_git_golden.json"),
            str(fixtures / "shared_public_readme_overrides.json"),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("complete export hashes and generated override passed") == 1


@pytest.mark.parametrize("template", ["max-miniapp-nextjs"])
def test_generated_file_and_empty_source_override_survive_export(template):
    # A shared-source path: the generated file must beat the template's own copy.
    generated_path = "src/app/omnia-brief.ts"
    generated = {"src/lib/utils.ts": "", generated_path: "export const brief = {};\n"}
    exported = project_export.build_runnable_export(template, generated)
    assert exported[generated_path] == generated[generated_path]
    assert exported["src/lib/utils.ts"] == ""
    for relative, entry in SOURCE_MAPPING.items():
        if template not in entry["templates"] or relative in generated:
            continue
        value = exported[relative]
        data = value.encode() if isinstance(value, str) else value
        assert hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest() == entry["sha256"]


@pytest.mark.parametrize("relative", ["src/../escape.ts", "/escape.ts", "src\\escape.ts"])
def test_api_rejects_unsafe_shared_source_path(tmp_path, monkeypatch, relative):
    template = tmp_path / "max-miniapp-nextjs"
    template.mkdir()
    shared = tmp_path / "shared-public"
    shared.mkdir()
    (shared / "manifest.json").write_text(
        json.dumps(
            {
                "templates": [template.name],
                "assets": [],
                "source_files": {relative: [template.name]},
            }
        )
    )
    monkeypatch.setattr(project_export, "_TEMPLATES_DIR", tmp_path)
    with pytest.raises(ValueError, match="shared source"):
        project_export.build_runnable_export(template.name, {})


@pytest.mark.parametrize("kind", ["ancestor", "leaf", "root", "missing"])
def test_api_rejects_unavailable_or_linked_canonical_sources(tmp_path, monkeypatch, kind):
    template = tmp_path / "max-miniapp-nextjs"
    template.mkdir()
    shared = tmp_path / "shared-public"
    canonical = shared / "source/src/lib/utils.ts"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("export const marker = 1;")
    (shared / "manifest.json").write_text(
        json.dumps(
            {
                "templates": [template.name],
                "assets": [],
                "source_files": {"src/lib/utils.ts": [template.name]},
            }
        )
    )
    monkeypatch.setattr(project_export, "_TEMPLATES_DIR", tmp_path)
    external = tmp_path / "outside.ts"
    external.write_text("external")
    try:
        if kind == "root":
            shared.rename(tmp_path / "outside")
            shared.symlink_to(tmp_path / "outside", target_is_directory=True)
        elif kind == "ancestor":
            canonical.parent.rename(tmp_path / "outside")
            canonical.parent.symlink_to(tmp_path / "outside", target_is_directory=True)
        else:
            canonical.unlink()
            if kind == "leaf":
                canonical.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    error = FileNotFoundError if kind == "missing" else ValueError
    with pytest.raises(error, match="shared"):
        project_export.build_runnable_export(template.name, {})
