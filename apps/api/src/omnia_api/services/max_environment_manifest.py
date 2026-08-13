"""Immutable, source-derived environment preflight for the MAX App Engineer."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from omnia_api.services.max_project_kit import MAX_MODEL_LOCKED_FILES

_TEMPLATE = (
    Path(__file__).resolve().parents[4]
    / "orchestrator"
    / "templates"
    / "max-miniapp-nextjs"
)
_SECRET_RE = re.compile(
    r"(?:secret|password|token|api[_-]?key|database_url|redis_url)\s*[:=]\s*[^,}\s]+",
    re.IGNORECASE,
)
_EXPORT_START_RE = re.compile(
    r"^export\s+(?:async\s+)?(?:function|const|type|interface)\s+",
    re.MULTILINE,
)


def _public_exports(source: str) -> list[str]:
    """Extract compact exact public declarations without implementation bodies."""

    lines = source.splitlines()
    exports: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _EXPORT_START_RE.match(line):
            index += 1
            continue
        chunk = [line.strip()]
        balance = line.count("{") - line.count("}")
        while index + 1 < len(lines) and (
            ("(" in " ".join(chunk) and ")" not in " ".join(chunk))
            or balance > 0
            or not chunk[-1].rstrip().endswith((";", "{", ")"))
        ):
            index += 1
            next_line = lines[index].strip()
            chunk.append(next_line)
            balance += next_line.count("{") - next_line.count("}")
            if len(chunk) >= 24:
                break
        declaration = " ".join(part for part in chunk if part)
        if "{" in declaration and declaration.startswith("export async function"):
            declaration = declaration.split("{", 1)[0].rstrip() + ";"
        exports.append(declaration[:1200])
        index += 1
    return exports


def build_max_environment_manifest() -> dict[str, Any]:
    package = json.loads((_TEMPLATE / "package.json").read_text(encoding="utf-8"))
    client = (_TEMPLATE / "src/lib/omnia/client.ts").read_text(encoding="utf-8")
    provider = (_TEMPLATE / "src/components/MaxAppProvider.tsx").read_text(
        encoding="utf-8"
    )
    config = (_TEMPLATE / "src/lib/omnia/max-config.ts").read_text(encoding="utf-8")
    index = (_TEMPLATE / ".omnia/skills/INDEX.md").read_text(encoding="utf-8")
    manifest: dict[str, Any] = {
        "version": 1,
        "runtime": {
            "framework": f"next@{package['dependencies']['next']}",
            "react": package["dependencies"]["react"],
            "typescript": package["devDependencies"]["typescript"],
            "package_manager": package["packageManager"],
            "node": package["engines"]["node"],
            "scripts": package["scripts"],
        },
        "installed_dependencies": {
            **package["dependencies"],
            **package["devDependencies"],
        },
        "writable_roots": [
            ".omnia/max-design-spec.json",
            "src/app/globals.css",
            "src/components/product/",
            "src/components/ (except locked)",
            "src/data/",
            "src/hooks/",
            "src/lib/product/",
            "src/store/",
            "src/styles/",
            "src/types/",
            "public/product/ (no executable JS/WASM)",
        ],
        "locked_paths": sorted(MAX_MODEL_LOCKED_FILES),
        "managed_signatures": {
            "integration_client": _public_exports(client),
            "max_provider": _public_exports(provider),
            "managed_config": _public_exports(config),
        },
        "capabilities": {
            "always": ["signed MAX identity", "Bridge", "user-scoped actions", "legal"],
            "runtime_discovery": (
                "await getOmniaIntegrations() for redacted provider/capability ids"
            ),
            "optional_degradation": (
                "If an optional provider is disconnected, keep the rest of the product complete "
                "and render an honest unavailable/configuration state. Never fake success."
            ),
            "required_external_block": (
                "Only a capability explicitly required by the brief may remain blocked by "
                "owner credentials, provider moderation/approval or a sustained external outage."
            ),
        },
        "proof_commands": [
            "pnpm typecheck",
            "runtime_check / through managed preview",
            "signed see at 360 and 390",
            "signed navigation/primary action/reload persistence gate",
        ],
        "constraints": [
            "No package installation or package.json edits; adapt to installed dependencies.",
            "No direct DB, API route, secret, initData validation or server runtime ownership.",
            "Read relevant locked contracts/docs/logs through read-only tools when an API differs.",
            "Internal compile/import/runtime/API mismatch is repair work, never a "
            "terminal blocker.",
            "Start with a usable vertical slice, then split complex products into small modules.",
        ],
        "skill_index": index,
    }
    rendered = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    if _SECRET_RE.search(rendered):
        raise RuntimeError("MAX environment manifest unexpectedly contains secret-shaped data")
    return manifest


def manifest_prompt_block() -> str:
    manifest = json.dumps(build_max_environment_manifest(), ensure_ascii=False, indent=2)
    return (
        "\n\nOMNIA MAX ENVIRONMENT MANIFEST (server-owned immutable preflight, source-derived):\n"
        "Study this before plan_task and before code. Do not guess dependencies or managed APIs. "
        "When compile/import/runtime/API evidence is red, reread the relevant exact contract/log, "
        "adapt with installed primitives and continue; internal platform mismatch is not a reason "
        "to stop. For a complex app, deliver one usable vertical slice first, then split screens, "
        "components, hooks and product services across continuation milestones.\n"
        f"```json\n{manifest}\n```"
    )


__all__ = ["build_max_environment_manifest", "manifest_prompt_block"]
