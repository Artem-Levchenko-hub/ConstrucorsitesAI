"""Contract guard for MAX template's opt-in, read-only code intelligence.

The builder copies this template verbatim. Keep analysis tools pinned and opt-in:
they must never silently rewrite generated customer code or turn ordinary builds
into network-backed vulnerability scans.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE = _ROOT / "templates" / "max-miniapp-nextjs"


def test_max_code_intelligence_toolchain_contract() -> None:
    package = json.loads((_TEMPLATE / "package.json").read_text(encoding="utf-8"))
    dev = package["devDependencies"]

    assert dev["oxlint"] == "1.78.0"
    assert dev["@ast-grep/cli"] == "0.45.1"
    assert dev["dependency-cruiser"] == "18.2.0"
    assert package["scripts"]["analyze:agent"] == "node scripts/analyze-code.mjs"
    assert package["scripts"]["security:agent"] == "node scripts/analyze-code.mjs --security"


def test_max_code_intelligence_script_is_bounded_and_read_only() -> None:
    script = (_TEMPLATE / "scripts" / "analyze-code.mjs").read_text(encoding="utf-8")
    config = (_TEMPLATE / ".dependency-cruiser.cjs").read_text(encoding="utf-8")

    for tool in ("oxlint", "depcruise", "ast-grep", "osv-scanner"):
        assert tool in script
    for key in (
        "diagnostics:",
        "security_findings:",
        "security_scan_completed:",
        "affected_files:",
        "counts:",
        "unavailable:",
    ):
        assert key in script
    assert "MAX_DIAGNOSTICS" in script
    assert "MAX_AFFECTED_FILES" in script
    assert "MAX_REPORT_BYTES" in script
    assert "Buffer.byteLength" in script
    assert "TOOL_TIMEOUT_MS" in script
    assert "couldNotResolve" in script
    assert "dependency.circular" in script
    assert 'replaceAll("\\\\", "/")' in script
    assert "--pattern" in script
    assert '"-L"' in script
    assert '"pnpm-lock.yaml"' in script
    assert "--rewrite" not in script
    assert "--fix" not in script
    assert '".dependency-cruiser.cjs"' in script
    assert 'name: "no-circular"' in config
    assert 'name: "not-to-unresolvable"' in config
    assert 'includeOnly: ["^src/"]' in config
    assert 'tsConfig: { fileName: "tsconfig.json" }' in config


def test_max_dev_image_pins_verified_osv_scanner_binary() -> None:
    dockerfile = (_TEMPLATE / "Dockerfile.dev").read_text(encoding="utf-8")
    production_dockerfile = (_TEMPLATE / "Dockerfile.prod").read_text(encoding="utf-8")

    assert "OSV_SCANNER_VERSION=2.5.0" in dockerfile
    assert "TARGETARCH" in dockerfile
    assert "edcfc41d257db36148f065055655fe3fcfc434b0b423ea67468a84c207524e0c" in dockerfile
    assert "fe152e1a546af223e6c557cc3111a8bb3e5dc02fcbf7dbe95d26567c0f0041f2" in dockerfile
    assert "osv-scanner" in dockerfile
    assert "pnpm install --frozen-lockfile ||" not in dockerfile
    assert "pnpm install --frozen-lockfile --prod=false ||" not in production_dockerfile


def test_analyzer_output_cap_preserves_late_blocker(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        raise AssertionError("Node.js is required to verify the managed analyzer")

    script_dir = tmp_path / "scripts"
    oxlint_dir = tmp_path / "node_modules" / "oxlint" / "dist"
    dependency_dir = tmp_path / "node_modules" / "dependency-cruiser" / "bin"
    script_dir.mkdir(parents=True)
    oxlint_dir.mkdir(parents=True)
    dependency_dir.mkdir(parents=True)
    shutil.copy2(_TEMPLATE / "scripts" / "analyze-code.mjs", script_dir / "analyze-code.mjs")
    (oxlint_dir / "cli.js").write_text(
        "console.log(JSON.stringify(Array.from({length:100},(_,i)=>({"
        "filePath:'src/'+String(i).padStart(3,'0')+'-'+('x'.repeat(480))+'.ts',"
        "messages:[{severity:1,message:'w'.repeat(1500),ruleId:'r'.repeat(250)}]}))))",
        encoding="utf-8",
    )
    (dependency_dir / "dependency-cruise.mjs").write_text(
        "console.log(JSON.stringify({modules:[{source:'src/late.ts',dependencies:[{"
        "resolved:'src/cycle.ts',circular:true,cycle:['src/late.ts']}]}]}))",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [node, str(script_dir / "analyze-code.mjs")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    report = json.loads(completed.stdout)

    assert len(completed.stdout.encode("utf-8")) <= 140_001
    assert any(
        item.get("severity") == "error" and item.get("tool") == "dependency-cruiser"
        for item in report["diagnostics"]
    )
