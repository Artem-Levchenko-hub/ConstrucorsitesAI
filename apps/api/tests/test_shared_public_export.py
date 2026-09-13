import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omnia_api.services import project_export

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "orchestrator/tests/fixtures/shared_public_git_golden.json"
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


def test_generic_complete_template_with_matching_name_keeps_its_own_files(tmp_path):
    template = tmp_path / "max-miniapp-nextjs"
    template.mkdir()
    (template / "package.json").write_text("{}")
    assert project_export.read_template_tree(template) == {"package.json": "{}"}


def test_actual_export_smoke_with_clean_mounted_templates_outside_checkout(tmp_path):
    """Run the CI entrypoint against isolated API source and Git-equivalent template bytes."""
    api = Path(__file__).resolve().parents[1]
    mounted = tmp_path / "orchestrator/templates"
    shared = project_export._TEMPLATES_DIR / "shared-public"
    shutil.copytree(shared, mounted / "shared-public")
    for asset in (mounted / "shared-public").iterdir():
        asset.write_bytes(asset.read_bytes().replace(b"\r\n", b"\n"))
    for template, expected in GOLDEN["templates"].items():
        for relative in expected:
            source = project_export._TEMPLATES_DIR / template / relative
            if not source.exists():
                source = shared / Path(relative).name
            target = mounted / template / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
    # Match the image's /app/src plus /orchestrator/templates relationship, with no
    # installed orchestrator or checkout on sys.path. This uses the real API reader.
    source_root = tmp_path / "app/src"
    service = source_root / "omnia_api/services/project_export.py"
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
    assert result.stdout.count("complete export hashes and generated override passed") == 4
