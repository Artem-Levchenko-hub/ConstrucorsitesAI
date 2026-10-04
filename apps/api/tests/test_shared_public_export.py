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
    ).read_text(encoding="utf-8")
)


DEPENDENCY_OVERRIDES = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "orchestrator/tests/fixtures/max_template_dependency_overrides.json"
    ).read_text(encoding="utf-8")
)

PREVIEW_RENEWAL_OVERRIDES = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "orchestrator/tests/fixtures/max_template_preview_renewal_overrides.json"
    ).read_text(encoding="utf-8")
)

ANALYTICS_OVERRIDES = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "orchestrator/tests/fixtures/max_template_analytics_overrides.json"
    ).read_text(encoding="utf-8")
)

SOURCE_MAPPING = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "orchestrator/tests/fixtures/shared_source_git_mapping.json"
    ).read_text(encoding="utf-8")
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
    for template, frozen in GOLDEN["templates"].items():
        expected = dict(frozen)
        if template == "max-miniapp-nextjs":
            expected.update(DEPENDENCY_OVERRIDES)
            expected.update(PREVIEW_RENEWAL_OVERRIDES)
            expected.update(ANALYTICS_OVERRIDES)
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
            str(fixtures / "max_template_dependency_overrides.json"),
            str(fixtures / "max_template_action_write_overrides.json"),
            str(fixtures / "max_template_support_overrides.json"),
            str(fixtures / "max_template_preview_renewal_overrides.json"),
            str(fixtures / "max_template_analytics_overrides.json"),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("complete export hashes and generated override passed") == 1

    # Dependency overrides must never turn into an SDK/source-hash bypass.
    untrusted = tmp_path / "untrusted-dependency-overrides.json"
    bad_overrides = dict(DEPENDENCY_OVERRIDES)
    sdk_relative = "src/lib/omnia/integration-client.ts"
    bad_overrides[sdk_relative] = {"sha256": "0" * 64, "mode": "100644"}
    untrusted.write_text(json.dumps(bad_overrides), encoding="utf-8")
    args = [
        sys.executable,
        str(script),
        str(fixtures / "shared_public_git_golden.json"),
        str(fixtures / "shared_public_readme_overrides.json"),
        str(untrusted),
        str(fixtures / "max_template_action_write_overrides.json"),
        str(fixtures / "max_template_support_overrides.json"),
        str(fixtures / "max_template_preview_renewal_overrides.json"),
        str(fixtures / "max_template_analytics_overrides.json"),
    ]
    rejected = subprocess.run(
        args, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
    )
    assert rejected.returncode != 0
    assert "unexpected MAX dependency override paths" in rejected.stderr

    # Action overrides are bounded to the five deliberately changed source/test paths.
    untrusted_actions = tmp_path / "untrusted-action-overrides.json"
    bad_actions = json.loads(
        (fixtures / "max_template_action_write_overrides.json").read_text(encoding="utf-8")
    )
    bad_actions["src/lib/omnia/unreviewed.ts"] = {"sha256": "0" * 64, "mode": "100644"}
    untrusted_actions.write_text(json.dumps(bad_actions), encoding="utf-8")
    bad_args = args.copy()
    bad_args[4] = str(fixtures / "max_template_dependency_overrides.json")
    bad_args[5] = str(untrusted_actions)
    rejected = subprocess.run(
        bad_args, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
    )
    assert rejected.returncode != 0
    assert "unexpected MAX action write override paths" in rejected.stderr

    # Preview overrides admit exactly four reviewed paths and retain byte equality.
    for extra_path in ("src/lib/omnia/unreviewed.ts", None):
        bad_preview = dict(PREVIEW_RENEWAL_OVERRIDES)
        if extra_path:
            bad_preview[extra_path] = {"sha256": "0" * 64, "mode": "100644"}
        else:
            bad_preview["src/lib/max/owner-preview-renewal.ts"] = {
                "sha256": "0" * 64,
                "mode": "100644",
            }
        invalid_preview = tmp_path / "untrusted-preview-overrides.json"
        invalid_preview.write_text(json.dumps(bad_preview), encoding="utf-8")
        invalid_args = args.copy()
        invalid_args[4] = str(fixtures / "max_template_dependency_overrides.json")
        invalid_args[7] = str(invalid_preview)
        rejected = subprocess.run(
            invalid_args, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
        )
        assert rejected.returncode != 0
        expected_error = (
            "unexpected MAX preview renewal override paths"
            if extra_path
            else "max-miniapp-nextjs/src/lib/max/owner-preview-renewal.ts"
        )
        assert expected_error in rejected.stderr

    # New receipt helper and changed routes remain an explicit four-file contract.
    for extra_path in ("src/lib/omnia/unreviewed.ts", None):
        bad_analytics = dict(ANALYTICS_OVERRIDES)
        if extra_path:
            bad_analytics[extra_path] = {"sha256": "0" * 64, "mode": "100644"}
        else:
            bad_analytics["src/lib/omnia/analytics.ts"] = {
                "sha256": "0" * 64,
                "mode": "100644",
            }
        invalid_analytics = tmp_path / "untrusted-analytics-overrides.json"
        invalid_analytics.write_text(json.dumps(bad_analytics), encoding="utf-8")
        invalid_args = args.copy()
        invalid_args[4] = str(fixtures / "max_template_dependency_overrides.json")
        invalid_args[8] = str(invalid_analytics)
        rejected = subprocess.run(
            invalid_args, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
        )
        assert rejected.returncode != 0
        expected_error = (
            "unexpected MAX analytics override paths"
            if extra_path
            else "max-miniapp-nextjs/src/lib/omnia/analytics.ts"
        )
        assert expected_error in rejected.stderr

    # A missing newly managed helper must fail the same baked-export gate.
    helper = mounted / "max-miniapp-nextjs/src/lib/omnia/analytics.ts"
    helper_bytes = helper.read_bytes()
    helper.unlink()
    valid_args = args.copy()
    valid_args[4] = str(fixtures / "max_template_dependency_overrides.json")
    rejected = subprocess.run(
        valid_args, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
    )
    assert rejected.returncode != 0
    assert "incomplete standalone export" in rejected.stderr
    helper.write_bytes(helper_bytes)

    # The actual shared SDK bytes must still match the reviewed immutable hash.
    sdk_source = mounted / "max-miniapp-nextjs" / sdk_relative
    sdk_source.write_bytes(sdk_source.read_bytes() + b"\n// qa-invalid-sdk-drift\n")
    args[4] = str(fixtures / "max_template_dependency_overrides.json")
    rejected = subprocess.run(
        args, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
    )
    assert rejected.returncode != 0
    assert f"max-miniapp-nextjs/{sdk_relative}" in rejected.stderr


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
