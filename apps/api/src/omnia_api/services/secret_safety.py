"""Deterministic guardrails for credentials submitted to the app builder.

Credentials belong in Omnia's encrypted Integration Hub, never in a generated
project repository.  Prompt instructions alone are not a sufficient boundary:
the writer itself must reject secret files and secret-shaped literals before a
container or git object ever sees them.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

_SECRET_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    re.compile(r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
)
_LABELLED_SECRET = re.compile(
    r"(?i)(\b(?:api[_ -]?(?:key|ключ)|token|secret|password|ключ|токен|пароль)"
    r"\s*(?::|=|—|-)\s*)([A-Za-z0-9][A-Za-z0-9._~+/=-]{15,})"
)
_BLOCKED_PACKAGE_SPEC = re.compile(
    r"(?i)(?:^[./\\]|://|^(?:file|link|workspace|portal|patch|exec|git|git\+ssh|"
    r"github|gitlab|bitbucket|ssh):)"
)
_PROTECTED_PACKAGE_SCRIPTS = {
    "dev": "next dev --turbopack --port 3000 --hostname 0.0.0.0",
    "build": "next build",
    "start": "next start --port 3000 --hostname 0.0.0.0",
}
_BLOCKED_LIFECYCLE_SCRIPTS = {
    "preinstall",
    "install",
    "postinstall",
    "prepare",
    "prepack",
    "postpack",
    "prepublish",
    "prepublishonly",
}
_SENSITIVE_ENV_REFERENCE = re.compile(
    r"(?i)process\.env(?:\s*\[|\.\s*[A-Z0-9_]*"
    r"(?:SECRET|TOKEN|PASSWORD|PASS|PRIVATE_KEY|ACCESS_KEY|API_KEY|DATABASE_URL))"
)
_ENV_ENUMERATION = re.compile(
    r"(?i)(?:Object\.(?:keys|values|entries)\s*\(\s*process\.env|"
    r"JSON\.stringify\s*\(\s*process\.env|\.\.\.\s*process\.env|"
    r"for\s*\([^)]*\bin\s+process\.env)"
)


def contains_provider_secret(value: str) -> bool:
    """Return true only for high-confidence credential shapes.

    The deliberately conservative patterns avoid intercepting ordinary product
    briefs that merely mention an API key or an environment-variable name.
    """

    candidate = value or ""
    return bool(_LABELLED_SECRET.search(candidate)) or any(
        pattern.search(candidate) for pattern in _SECRET_TOKEN_PATTERNS
    )


def redact_provider_secrets(value: str) -> str:
    """Remove recognised credentials before chat persistence or display."""

    redacted = _LABELLED_SECRET.sub(
        lambda match: f"{match.group(1)}[CREDENTIAL REDACTED]",
        value or "",
    )
    for pattern in _SECRET_TOKEN_PATTERNS:
        redacted = pattern.sub("[CREDENTIAL REDACTED]", redacted)
    return redacted


def is_secret_file(path: str) -> bool:
    """Detect repository paths that may contain runtime credentials."""

    normalized = (path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    name = PurePosixPath(normalized).name.lower()
    return (
        name == ".env"
        or name.startswith(".env.")
        or name
        in {
            "secrets.json",
            "secrets.yaml",
            "secrets.yml",
        }
    )


def _package_manifest_rejection(candidate: str) -> str | None:
    try:
        manifest = json.loads(candidate)
    except (TypeError, ValueError):
        return "package.json must contain a valid JSON object."
    if not isinstance(manifest, dict):
        return "package.json must contain a valid JSON object."
    if manifest.get("packageManager") != "pnpm@9.15.0":
        return "package.json packageManager is platform-managed and cannot change."
    if any(key in manifest for key in ("pnpm", "overrides", "resolutions")):
        return "package manager overrides and local patches are not allowed."

    scripts = manifest.get("scripts", {})
    if not isinstance(scripts, dict):
        return "package.json scripts must be an object."
    for name, expected in _PROTECTED_PACKAGE_SCRIPTS.items():
        if scripts.get(name) != expected:
            return f"package.json script {name!r} is platform-managed and cannot change."
        if f"pre{name}" in scripts or f"post{name}" in scripts:
            return f"package.json pre{name}/post{name} hooks are not allowed."
    if _BLOCKED_LIFECYCLE_SCRIPTS.intersection(scripts):
        return "package lifecycle hooks are not allowed in generated MAX projects."

    for section in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ):
        dependencies = manifest.get(section, {})
        if not isinstance(dependencies, dict):
            return f"package.json {section} must be an object."
        for package, spec in dependencies.items():
            if not isinstance(package, str) or not package or not isinstance(spec, str):
                return f"package.json {section} entries must be string package versions."
            if len(package) > 214 or len(spec) > 256 or _BLOCKED_PACKAGE_SPEC.search(spec):
                return (
                    f"dependency {package!r} must use an npm-registry version, "
                    "not a URL, git, file, workspace or executable source."
                )
    return None


def max_model_write_rejection(path: str, candidate: str) -> str | None:
    """Explain why a MAX model write is unsafe, or return ``None``."""

    if is_secret_file(path):
        return (
            "Credential files are managed by Omnia and cannot be written by the "
            "generation agent. Use the Studio Integration Hub; never create .env files."
        )
    if contains_provider_secret(candidate):
        return (
            "A provider credential was detected and the write was blocked before it "
            "reached the project repository. Use the Studio Integration Hub instead."
        )
    if _SENSITIVE_ENV_REFERENCE.search(candidate) or _ENV_ENUMERATION.search(candidate):
        return (
            "Runtime credentials cannot be read or enumerated from generated MAX "
            "product code. Use the managed DB/session/integration modules instead."
        )
    normalized = (path or "").replace("\\", "/").lstrip("./")
    if normalized == "package.json":
        return _package_manifest_rejection(candidate)
    return None


__all__ = [
    "contains_provider_secret",
    "is_secret_file",
    "max_model_write_rejection",
    "redact_provider_secrets",
]
