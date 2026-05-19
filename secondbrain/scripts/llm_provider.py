"""Unified LLM provider layer for SecondBrain.

Backends:
- cursor: tries Cursor CLI agent command
- claude: uses claude-agent-sdk
- gemini: direct Google AI Studio REST call (with optional UK proxy)
- none: disabled
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    import httpx  # type: ignore  # noqa: F401  used only in gemini backend
except Exception:  # pragma: no cover — httpx optional unless gemini is the chosen backend
    httpx = None  # type: ignore[assignment]


@dataclass
class LLMResult:
    ok: bool
    backend: str
    text: str
    error: str = ""
    cost_usd: float = 0.0


def _classify_error(message: str) -> str:
    text = message.lower()
    if "out of extra usage" in text or "rate limit" in text:
        return "quota_exceeded"
    if "forbidden" in text or "request not allowed" in text or "failed to authenticate" in text:
        return "auth_forbidden"
    if "not found" in text and ("agent" in text or "claude" in text):
        return "cli_not_found"
    return "runtime_error"


def _run_cursor_cli(prompt: str, cwd: Path, max_turns: int) -> LLMResult:
    override = os.environ.get("SECOND_BRAIN_CURSOR_AGENT_CMD", "").strip()
    candidates: list[list[str]] = []
    if override:
        candidates.append(shlex.split(override))
    candidates.extend([["agent"], ["cursor", "agent"]])

    suffix = ["-p", prompt, "--output-format", "text", "--mode", "ask", "--max-turns", str(max_turns)]

    for base in candidates:
        cmd = [*base, *suffix]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=120,
                check=False,
            )
        except FileNotFoundError:
            continue
        except Exception as exc:
            return LLMResult(False, "cursor", "", error=f"cursor_cli_error:{exc}")

        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if "Warning: 'p' is not in the list of known options" in combined:
            # Desktop cursor.exe command parser, not headless agent CLI.
            continue

        if proc.returncode == 0 and (proc.stdout or "").strip():
            return LLMResult(True, "cursor", proc.stdout.strip())

        if proc.returncode != 0 and combined.strip():
            return LLMResult(
                False,
                "cursor",
                "",
                error=f"{_classify_error(combined)}: {combined.strip()[:1200]}",
            )

    return LLMResult(False, "cursor", "", error="cli_not_found: Cursor agent CLI is not available")


async def _run_claude_async(prompt: str, cwd: Path, max_turns: int) -> LLMResult:
    try:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
    except Exception as exc:
        return LLMResult(False, "claude", "", error=f"sdk_import_error:{exc}")

    text = ""
    cost = 0.0
    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(cwd),
                allowed_tools=[],
                max_turns=max_turns,
                stderr=lambda _s: None,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text += block.text
            elif isinstance(message, ResultMessage):
                cost = float(message.total_cost_usd or 0.0)
    except Exception as exc:
        msg = str(exc)
        return LLMResult(False, "claude", "", error=f"{_classify_error(msg)}: {msg}")

    if not text.strip():
        return LLMResult(False, "claude", "", error="empty_response")
    return LLMResult(True, "claude", text.strip(), cost_usd=cost)


def _find_bundled_claude() -> str | None:
    """Find the bundled claude.exe from claude_agent_sdk."""
    try:
        import claude_agent_sdk
        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude.exe"
        if bundled.exists():
            return str(bundled)
    except Exception:
        pass
    return None


def _run_claude_cli_direct(prompt: str, cwd: Path, max_turns: int) -> LLMResult:
    """Direct subprocess call to bundled claude CLI — works even in detached hooks.
    Uses --tools "" to disable tool use: pure text generation, always finishes in 1 turn.
    """
    exe = _find_bundled_claude()
    if not exe:
        return LLMResult(False, "claude_cli", "", error="bundled_not_found")
    cmd = [exe, "-p", prompt, "--output-format", "text", "--tools", "", "--max-turns", str(max_turns)]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=120,
            check=False,
        )
    except Exception as exc:
        return LLMResult(False, "claude_cli", "", error=f"cli_error:{exc}")
    if proc.returncode == 0 and (proc.stdout or "").strip():
        return LLMResult(True, "claude_cli", proc.stdout.strip())
    combined = (proc.stdout or "") + (proc.stderr or "")
    return LLMResult(False, "claude_cli", "", error=f"{_classify_error(combined)}: {combined.strip()[:800]}")


def _run_gemini(prompt: str, cwd: Path, max_turns: int) -> LLMResult:
    """Direct REST call to Google AI Studio. Reads:
       - GEMINI_API_KEY (mandatory)
       - SECOND_BRAIN_GEMINI_MODEL (optional, default 'gemini-2.5-flash')
       - GEMINI_HTTPS_PROXY (optional — UK proxy used on the VPS to bypass
         Google's RU geo-block; same format http://user:pass@host:port)
    The Gemini 2.5 family is a thinking model; we force `thinkingBudget: 0`
    via `generationConfig.thinkingConfig` so the entire token budget goes to
    visible output (same workaround as in apps/llm-gateway).
    `cwd` and `max_turns` are accepted for signature parity with other
    backends but Gemini's REST flow doesn't use them.
    """
    del cwd, max_turns  # signature parity only
    if httpx is None:
        return LLMResult(False, "gemini", "", error="httpx_not_installed")
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return LLMResult(False, "gemini", "", error="missing_gemini_api_key")

    model = os.environ.get("SECOND_BRAIN_GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    proxy = os.environ.get("GEMINI_HTTPS_PROXY", "").strip() or None

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    try:
        with httpx.Client(proxy=proxy, timeout=120.0) as client:
            resp = client.post(url, json=body)
    except Exception as exc:
        return LLMResult(False, "gemini", "", error=f"{_classify_error(str(exc))}: {exc}")

    if resp.status_code >= 400:
        msg = (resp.text or "")[:1200]
        return LLMResult(False, "gemini", "", error=f"{_classify_error(msg)}: {resp.status_code} {msg}")

    try:
        data = resp.json()
    except Exception as exc:
        return LLMResult(False, "gemini", "", error=f"json_decode_error:{exc}")

    candidates = data.get("candidates") or []
    if not candidates:
        # Most often this is safety-block; surface the reason.
        feedback = data.get("promptFeedback", {})
        return LLMResult(False, "gemini", "", error=f"empty_response: {json.dumps(feedback)[:300]}")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        finish = candidates[0].get("finishReason", "")
        return LLMResult(False, "gemini", "", error=f"empty_response: finish_reason={finish}")
    return LLMResult(True, "gemini", text)


def run_llm_text(prompt: str, cwd: Path, max_turns: int = 2) -> LLMResult:
    backend = os.environ.get("SECOND_BRAIN_LLM_BACKEND", "claude").strip().lower()
    if backend in {"", "auto"}:
        backend = "claude"

    if backend == "none":
        return LLMResult(False, "none", "", error="backend_disabled")

    if backend == "cursor":
        return _run_cursor_cli(prompt, cwd, max_turns)

    if backend == "gemini":
        return _run_gemini(prompt, cwd, max_turns)

    if backend == "claude":
        # Try async SDK first; fall back to direct CLI (works in detached subprocesses)
        result = asyncio.run(_run_claude_async(prompt, cwd, max_turns))
        if not result.ok:
            result = _run_claude_cli_direct(prompt, cwd, max_turns)
        return result

    return LLMResult(False, backend, "", error=f"unknown_backend:{backend}")


def parse_json_from_text(text: str) -> dict | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None

