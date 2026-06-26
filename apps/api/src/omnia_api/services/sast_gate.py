"""SAST gate (K3a) — deterministic static scan for the top AI-code CWEs.

Sibling to ``security_gate`` (G005, which checks the RUNTIME leak/transport
surface): this is the STATIC source tier — defense-in-depth the knowledge-layer
plan calls for (docs/plans/knowledge-layer.md §3.1).

Research (deep-research 2026-06-26): prompt engineering ALONE does not reliably
reduce vulnerabilities ([F1] arXiv 2605.24298) and 12.1% of real AI-generated
files carry >=1 CWE ([F3] arXiv 2510.26103). The five heaviest classes by mean
CVSS ([F4] arXiv 2510.26103 Table 7) are exactly the ones a static scanner
catches cheaply:

    CWE-89  SQL injection            CWE-78  OS command injection
    CWE-94  code injection           CWE-798 hard-coded credentials
    CWE-259 hard-coded password

The project's «engine CAN enforce, model WON'T follow the prompt» lesson applied
to security: a deterministic gate, not an instruction. Mirrors
``backend_guardrail`` — a PURE scan over the writer's generated files
(:func:`scan_sast` / :func:`summarize` / :func:`check_sast`), unit-tested
without a container, fed into the same ``agent_gate_feedback`` self-heal loop.

Scope + honesty (research caveats): this is the CHEAP regex tier (precision over
recall), NOT a full SAST. It targets high-confidence, high-severity sinks +
strong secret shapes with low false-positive risk (a noisy gate wastes heal
runs). It is NECESSARY-not-sufficient — the heavier Semgrep
(p/owasp-top-ten + p/secrets) + gitleaks tier runs IN the dev container and is a
follow-up (needs the tool in the image; same plan §3.1). Gated by
``Settings.use_sast_gate`` (run + advisory log) and
``Settings.sast_gate_blocking`` (whether a finding blocks/heals). Both default
OFF → prod generation byte-unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Writer-authored files worth scanning (`files` already excludes the fixed
# template/engine, so engine code with legitimate patterns is never scanned).
_SCANNED = re.compile(r"\.(ts|tsx|mts|cts|js|jsx|mjs|cjs|py)$")
_PY = re.compile(r"\.py$")

# ── High-confidence, high-severity sinks (low false-positive) ────────────────
_JS_SINKS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\beval\s*\("), "CWE-94", "eval() — code injection sink"),
    (re.compile(r"\bnew\s+Function\s*\("), "CWE-94", "new Function() — code injection sink"),
    (re.compile(r"\b(exec|execSync|spawn|spawnSync)\s*\(\s*[`'\"][^`'\")]*\$\{"),
     "CWE-78", "child_process exec with string interpolation — command injection"),
    (re.compile(r"\b(exec|execSync)\s*\(\s*[^)]*\+[^)]*\)"),
     "CWE-78", "child_process exec with string concatenation — command injection"),
    (re.compile(r"dangerouslySetInnerHTML"), "CWE-79",
     "dangerouslySetInnerHTML — XSS sink (use the kit / sanitize)"),
    (re.compile(r"`[^`]*\b(SELECT|INSERT|UPDATE|DELETE|DROP)\b[^`]*\$\{", re.IGNORECASE),
     "CWE-89", "SQL built by string interpolation — injection (parameterize / use the SDK)"),
)

_PY_SINKS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\beval\s*\("), "CWE-94", "eval() — code injection sink"),
    (re.compile(r"\bexec\s*\("), "CWE-94", "exec() — code injection sink"),
    (re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), "CWE-78",
     "subprocess(..., shell=True) — command injection"),
    (re.compile(r"\bos\.system\s*\("), "CWE-78", "os.system() — command injection"),
    (re.compile(r"(execute|executemany)\s*\(\s*f[\"']", re.IGNORECASE), "CWE-89",
     "SQL via f-string — injection (use bound parameters)"),
    (re.compile(r"(execute|executemany)\s*\([^)]*%\s*\(", re.IGNORECASE), "CWE-89",
     "SQL via %-formatting — injection (use bound parameters)"),
)

# ── Hard-coded secrets (CWE-798 / CWE-259) — strong token shapes ─────────────
_SECRET_TOKENS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
     "private key committed in source"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "OpenAI-style secret key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "GitHub personal access token"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), "Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "Google API key"),
)

# A secret-named var assigned a STRING LITERAL (value captured → env refs /
# placeholders are filtered out so only a real literal secret trips it).
_SECRET_ASSIGN = re.compile(
    r"""(?ix)
    \b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|
       auth[_-]?token|client[_-]?secret|private[_-]?key|db[_-]?password)
    \s*[:=]\s*
    (["'])(?P<val>[^"'\n]{6,})\2
    """
)

_PLACEHOLDER = re.compile(
    r"(?i)(your[_-]?|example|placeholder|changeme|change[_-]?this|dummy|sample|"
    r"todo|fixme|xxx+|\.\.\.|<.+>|\$\{|process\.env|import\.meta|os\.environ|getenv|test[_-]?)"
)


@dataclass
class Finding:
    path: str
    cwe: str
    rule: str

    # `.ok`/`.name`/`.detail` so a Finding can also feed
    # agent_gate_feedback.outcome_from_checks (same shape as functional_gate.Check).
    @property
    def ok(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return f"{self.cwe} {self.path}"

    @property
    def detail(self) -> str:
        return self.rule


@dataclass
class SastVerdict:
    safe: bool
    findings: list[Finding] = field(default_factory=list)
    summary: str = ""


def _looks_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER.search(value)) or len(set(value)) <= 2


def scan_sast(files: dict[str, str]) -> list[Finding]:
    """Flag high-confidence security sinks + hard-coded secrets in writer files.

    `files` maps repo-relative path -> content. Pure + deterministic — no
    container, no network. Conservative (precision over recall); the in-container
    Semgrep tier covers the long tail."""
    findings: list[Finding] = []
    for raw_path, content in files.items():
        if not _SCANNED.search(raw_path):
            continue
        path = raw_path.replace("\\", "/")
        sinks = _PY_SINKS if _PY.search(raw_path) else _JS_SINKS
        for pattern, cwe, rule in sinks:
            if pattern.search(content):
                findings.append(Finding(path=path, cwe=cwe, rule=rule))
        for pattern, rule in _SECRET_TOKENS:
            if pattern.search(content):
                findings.append(Finding(path=path, cwe="CWE-798", rule=rule))
        for m in _SECRET_ASSIGN.finditer(content):
            if not _looks_placeholder(m.group("val")):
                findings.append(Finding(
                    path=path, cwe="CWE-798",
                    rule="hard-coded credential (move to an env var)",
                ))
                break  # one per file is enough signal
    return findings


def summarize(findings: list[Finding]) -> SastVerdict:
    """Aggregate findings into a verdict. Pure — unit-testable. Safe iff zero
    findings."""
    if not findings:
        return SastVerdict(
            safe=True, findings=[],
            summary="SAST gate OK — no high-confidence findings",
        )
    cwes = ", ".join(sorted({f.cwe for f in findings}))
    where = ", ".join(sorted({f.path for f in findings}))
    return SastVerdict(
        safe=False,
        findings=findings,
        summary=f"SAST gate FAILED — {len(findings)} finding(s) [{cwes}] in: {where}",
    )


def check_sast(files: dict[str, str]) -> SastVerdict:
    """Convenience: scan + summarize in one call (what the gate calls)."""
    return summarize(scan_sast(files))
