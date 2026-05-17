"""Background memory flush for Cursor session transcripts."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import COMPILE_AFTER_HOUR, DAILY_DIR, SCRIPTS_DIR, ROOT_DIR
from llm_provider import run_llm_text
from utils import ensure_structure, file_hash, load_flush_state, save_flush_state

os.environ["CLAUDE_INVOKED_BY"] = "secondbrain_flush"

LOG_FILE = SCRIPTS_DIR / "flush.log"
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

MAX_TURNS = 40
MAX_CONTEXT_CHARS = 22_000
DEDUP_WINDOW_SECONDS = 45


def _load_payload(payload_file: Path) -> dict:
    try:
        return json.loads(payload_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_content_blocks(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
        return "\n".join(part for part in text_parts if part)
    if isinstance(content, dict) and content.get("type") == "text":
        return str(content.get("text", ""))
    return ""


def extract_transcript_context(transcript_path: Path) -> tuple[str, int]:
    turns: list[str] = []
    with transcript_path.open(encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue

            role = entry.get("role")
            message = entry.get("message")
            if isinstance(message, dict):
                role = message.get("role", role)
                content = _extract_content_blocks(message.get("content", ""))
            else:
                content = _extract_content_blocks(entry.get("content", ""))

            if role not in {"user", "assistant"}:
                continue
            content = content.strip()
            if not content:
                continue
            label = "User" if role == "user" else "Assistant"
            turns.append(f"**{label}:** {content}")

    recent = turns[-MAX_TURNS:]
    context = "\n\n".join(recent)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
        split_idx = context.find("**")
        if split_idx > 0:
            context = context[split_idx:]
    return context, len(recent)


def append_daily_entry(content: str, section: str) -> Path:
    ensure_structure()
    now = datetime.now(timezone.utc).astimezone()
    daily_path = DAILY_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    if not daily_path.exists():
        daily_path.write_text(
            f"# Daily Log: {now.strftime('%Y-%m-%d')}\n\n## Sessions\n\n## Docs Ingest\n\n## Memory Maintenance\n\n",
            encoding="utf-8",
        )
    block = f"### {section} ({now.strftime('%H:%M')})\n\n{content.strip()}\n\n"
    with daily_path.open("a", encoding="utf-8") as f:
        f.write(block)
    return daily_path


def _fallback_extract(context: str) -> str:
    lines = [line.strip() for line in context.splitlines() if line.strip()]
    users = [line for line in lines if line.startswith("**User:**")]
    assistants = [line for line in lines if line.startswith("**Assistant:**")]
    sample_user = users[-3:] if users else []
    sample_assistant = assistants[-3:] if assistants else []
    bullets = "\n".join(f"- {s[:300]}" for s in sample_user + sample_assistant)
    if not bullets:
        return "FLUSH_OK"
    return (
        "**Context:** Session activity captured from transcript.\n\n"
        "**Key Exchanges:**\n"
        f"{bullets}\n\n"
        "**Decisions Made:**\n"
        "- Requires manual compile step if SDK is unavailable.\n"
    )


def llm_extract(context: str) -> str:
    prompt = f"""Summarize the conversation context below for a daily engineering memory log.

Return concise markdown with these sections when relevant:
- **Context**
- **Key Exchanges**
- **Decisions Made**
- **Lessons Learned**
- **Action Items**

Do not include routine tool noise.
If nothing is worth saving, return exactly: FLUSH_OK

## Conversation Context
{context}
"""
    result = run_llm_text(prompt, ROOT_DIR, max_turns=2)
    if not result.ok:
        logging.warning("LLM extraction failed (%s): %s", result.backend, result.error)
        return _fallback_extract(context)
    return result.text.strip() or "FLUSH_OK"


def maybe_trigger_compile() -> None:
    now = datetime.now(timezone.utc).astimezone()
    if now.hour < COMPILE_AFTER_HOUR:
        return
    compile_script = SCRIPTS_DIR / "compile.py"
    sync_script = SCRIPTS_DIR / "sync_project_docs.py"
    if not compile_script.exists():
        return

    if sync_script.exists():
        try:
            subprocess.run(
                [sys.executable, str(sync_script)],
                cwd=str(ROOT_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            logging.exception("sync_project_docs.py failed before compile")

    cmd = [sys.executable, str(compile_script)]
    kwargs: dict = {"cwd": str(ROOT_DIR)}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW only — DETACHED_PROCESS breaks Agent SDK subprocess I/O
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    log_handle = open(str(SCRIPTS_DIR / "compile.log"), "a", encoding="utf-8")
    subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT, **kwargs)
    logging.info("compile.py triggered after %s:00", COMPILE_AFTER_HOUR)


def maybe_update_context() -> None:
    """If context-dirty.json exists, spawn update_context.py in background."""
    dirty_file = SCRIPTS_DIR / "context-dirty.json"
    if not dirty_file.exists():
        return
    update_script = SCRIPTS_DIR / "update_context.py"
    if not update_script.exists():
        return
    cmd = [sys.executable, str(update_script)]
    kwargs: dict = {"cwd": str(ROOT_DIR)}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    log_handle = open(str(SCRIPTS_DIR / "context-update.log"), "a", encoding="utf-8")
    subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT, **kwargs)
    logging.info("update_context.py triggered")


def should_skip_flush(conversation_id: str, transcript_path: Path, event_name: str) -> bool:
    state = load_flush_state()
    now_ts = time.time()
    transcript_sig = ""
    if transcript_path.exists():
        transcript_sig = file_hash(transcript_path)

    if (
        state.get("conversation_id") == conversation_id
        and state.get("event_name") == event_name
        and state.get("transcript_sig") == transcript_sig
        and now_ts - float(state.get("timestamp", 0)) < DEDUP_WINDOW_SECONDS
    ):
        return True

    save_flush_state(
        {
            "conversation_id": conversation_id,
            "event_name": event_name,
            "transcript_sig": transcript_sig,
            "timestamp": now_ts,
        }
    )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Flush transcript memory into daily log")
    parser.add_argument("--payload-file", required=True, help="Path to hook payload json")
    parser.add_argument("--event", default="sessionEnd", help="Hook event name")
    args = parser.parse_args()

    ensure_structure()
    payload_path = Path(args.payload_file)
    payload = _load_payload(payload_path)
    payload_path.unlink(missing_ok=True)
    conversation_id = str(payload.get("conversation_id") or payload.get("session_id") or "unknown")
    transcript = payload.get("transcript_path") or os.environ.get("CURSOR_TRANSCRIPT_PATH")
    if not transcript:
        append_daily_entry("FLUSH_SKIP - transcript path missing.", "Memory Flush")
        return 0

    transcript_path = Path(transcript)
    if not transcript_path.exists():
        append_daily_entry(f"FLUSH_SKIP - transcript not found: `{transcript_path}`", "Memory Flush")
        return 0

    if should_skip_flush(conversation_id, transcript_path, args.event):
        logging.info("Skip duplicate flush for %s", conversation_id)
        return 0

    context, turns = extract_transcript_context(transcript_path)
    if turns == 0 or not context.strip():
        append_daily_entry("FLUSH_OK - no extractable user/assistant turns.", "Memory Flush")
        return 0

    extracted = llm_extract(context)
    if extracted.strip() == "FLUSH_OK":
        append_daily_entry("FLUSH_OK - nothing meaningful to store.", "Memory Flush")
    else:
        append_daily_entry(extracted, "Session")

    maybe_trigger_compile()
    maybe_update_context()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

