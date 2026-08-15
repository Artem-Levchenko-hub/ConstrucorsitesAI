from __future__ import annotations

import json

import pytest

from omnia_orchestrator.routers import runtime
from omnia_orchestrator.routers.runtime import _normalize_code_intelligence


def test_code_intelligence_report_is_bounded_and_normalized() -> None:
    raw = json.dumps(
        {
            "diagnostics": [
                {
                    "tool": "oxlint",
                    "rule": "typescript/no-explicit-any",
                    "severity": "error",
                    "file": "src\\app.tsx",
                    "message": "x" * 900,
                }
            ]
            * 60,
            "affected_files": ["../secret", "src\\other.ts"],
            "security_findings": [
                {
                    "tool": "osv-scanner",
                    "rule": "GHSA-test",
                    "severity": "error",
                    "file": "pnpm-lock.yaml",
                    "message": "vulnerable package",
                }
            ],
            "security_scan_completed": True,
            "unavailable": [{"tool": "depcruise", "reason": "not installed"}],
        }
    )

    result = _normalize_code_intelligence(raw)

    assert len(result["diagnostics"]) == 30
    assert result["diagnostics"][0]["file"] == "src/app.tsx"
    assert len(result["diagnostics"][0]["message"]) == 500
    assert result["affected_files"] == ["src/app.tsx", "src/other.ts"]
    assert result["root_cause_hint"] == "x" * 500
    assert len(result["evidence"]) == 20
    assert result["analysis_unavailable"] == ["depcruise: not installed"]
    assert result["security_scan_completed"] is True
    assert result["security_findings"][0]["code"] == "GHSA-test"


def test_code_intelligence_invalid_json_fails_soft() -> None:
    assert _normalize_code_intelligence("not-json") == {
        "analysis_unavailable": ["analyze-code: invalid JSON"]
    }


@pytest.mark.parametrize("findings", [None, "invalid", [None]])
def test_security_completion_rejects_malformed_findings(findings: object) -> None:
    result = _normalize_code_intelligence(
        json.dumps(
            {
                "diagnostics": [],
                "security_findings": findings,
                "security_scan_completed": True,
            }
        )
    )

    assert result["security_scan_completed"] is False
    assert "osv-scanner: invalid security findings report" in result["analysis_unavailable"]


@pytest.mark.asyncio
async def test_build_runs_analyzer_only_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    async def no_activity(_project_id: str) -> None:
        return None

    async def no_dep_doctor(_container: str) -> str:
        return ""

    async def exec_cmd(_container: str, *, cmd: list[str], **kwargs: object) -> dict[str, str]:
        commands.append(cmd)
        if cmd[0] == "node":
            return {
                "exit_code": "0",
                "stdout": json.dumps(
                    {
                        "diagnostics": [
                            {
                                "tool": "oxlint",
                                "rule": "no-debugger",
                                "severity": "error",
                                "file": "src/components/product/ProductApp.tsx",
                                "message": "debugger statement",
                            }
                        ],
                        "affected_files": [],
                        "unavailable": [],
                    }
                ),
                "stderr": "",
            }
        return {"exit_code": "0", "stdout": "", "stderr": ""}

    monkeypatch.setattr(runtime, "_verify_token", lambda _token: None)
    monkeypatch.setattr(runtime, "record_activity", no_activity)
    monkeypatch.setattr(runtime, "_run_dep_doctor", no_dep_doctor)
    monkeypatch.setattr(runtime, "exec_cmd", exec_cmd)

    legacy = await runtime.agent_build("project", "slug", code_intelligence=False)
    assert legacy == {"ok": True, "detail": "typecheck clean"}
    assert not any(command[0] == "node" for command in commands)

    enriched = await runtime.agent_build("project", "slug", code_intelligence=True)
    assert enriched["ok"] is False
    assert "blocking diagnostic" in enriched["detail"]
    assert enriched["diagnostics"][0]["code"] == "no-debugger"
    assert ["node", "/app/scripts/analyze-code.mjs"] in commands

    await runtime.agent_build("project", "slug", security_scan=True)
    assert ["node", "/app/scripts/analyze-code.mjs", "--security"] in commands


@pytest.mark.asyncio
async def test_missing_analyzer_keeps_typecheck_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exec_cmd(_container: str, *, cmd: list[str], **kwargs: object) -> dict[str, str]:
        return (
            {"exit_code": "127", "stdout": "", "stderr": "not found"}
            if cmd[0] == "node"
            else {"exit_code": "0", "stdout": "", "stderr": ""}
        )

    monkeypatch.setattr(runtime, "_verify_token", lambda _token: None)

    async def no_dep_doctor(_container: str) -> str:
        return ""

    async def no_activity(_project: str) -> None:
        return None

    monkeypatch.setattr(runtime, "record_activity", no_activity)
    monkeypatch.setattr(runtime, "_run_dep_doctor", no_dep_doctor)
    monkeypatch.setattr(runtime, "exec_cmd", exec_cmd)

    result = await runtime.agent_build("project", "slug", code_intelligence=True)

    assert result["ok"] is True
    assert result["detail"] == "typecheck clean"
    assert result["analysis_unavailable"]


def test_code_intelligence_rejects_absolute_and_nested_traversal_paths() -> None:
    result = _normalize_code_intelligence(
        json.dumps(
            {
                "diagnostics": [
                    {"tool": "oxlint", "file": "/etc/passwd", "message": "absolute"},
                    {"tool": "oxlint", "file": "src/../../secret", "message": "traversal"},
                    {"tool": "oxlint", "file": "src/app.tsx", "message": "safe"},
                ]
            }
        )
    )

    assert result["affected_files"] == ["src/app.tsx"]


def test_code_intelligence_prioritizes_late_blocker_over_advisories() -> None:
    result = _normalize_code_intelligence(
        json.dumps(
            {
                "diagnostics": [
                    {
                        "tool": "oxlint",
                        "severity": "warning",
                        "message": f"advisory {index}",
                    }
                    for index in range(30)
                ]
                + [
                    {
                        "tool": "dependency-cruiser",
                        "severity": "error",
                        "message": "late circular dependency",
                    }
                ]
            }
        )
    )

    assert len(result["diagnostics"]) == 30
    assert result["diagnostics"][0]["severity"] == "error"
    assert result["root_cause_hint"] == "late circular dependency"
