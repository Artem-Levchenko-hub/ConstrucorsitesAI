"""Bounded, source-bound recovery of stalled MAX native model turns."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Literal

from yleum_api.services.agent_builder import Action
from yleum_api.services.agent_progress import redact_sensitive_text
from yleum_api.services.generation.recovery_trace import bounded_recovery_diagnostic
from yleum_api.services.max_project_kit import MAX_MODEL_LOCKED_FILES
from yleum_api.services.secret_safety import contains_provider_secret, max_model_write_rejection

NativeRecoveryMode = Literal["inspection", "compiler", "output_limit", "transcript"]
INSPECTION_TOOLS = frozenset({"read_file", "list_dir", "grep", "read_logs"})
_ENTRY = "src/app/page.tsx"
_MAX_BRIEF_BYTES = 12_000
_MAX_SOURCE_BYTES = 18_000
_MAX_SNIPPET_BYTES = 6_000
_COMPILER_LINE = re.compile(
    r"^ *(?:\./|/workspace/)?(?P<path>src/[\w./-]+)"
    r"(?:\(\d+,\d+\)|:\d+(?::\d+)?): *"
    r"(?P<message>error TS\d{4}: .+)$",
    re.MULTILINE,
)


def compiler_failure(diagnostic: str) -> tuple[str, str] | None:
    match = _COMPILER_LINE.search(diagnostic)
    if match is None:
        return None
    # A line move is not evidence that the failed approach changed.
    identity = hashlib.sha256(
        f"{match['path']}\n{match['message']}".encode("utf-8", errors="replace")
    ).hexdigest()
    return match["path"], identity


def inspection_signature(actions: Sequence[Action], results: Sequence[str]) -> str:
    """Ignore provider tool IDs, call order, and equivalent workspace path spellings."""
    batch = []
    for action, result in zip(actions, results, strict=True):
        args = dict(action.args)
        if action.name in {"read_file", "list_dir", "grep"}:
            path = args.get("path", ".")
            if isinstance(path, str):
                args["path"] = posixpath.normpath(path.replace("\\", "/"))
        if action.name == "read_file" and args.get("start_line") == 1:
            args.pop("start_line")
        batch.append(json.dumps([action.name, args, result], sort_keys=True, ensure_ascii=True))
    payload = json.dumps(sorted(set(batch)), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def repeated_inspection(history: list[str]) -> bool:
    return (
        (len(history) >= 2 and history[-1] == history[-2])
        or (len(history) == 4 and history[:2] == history[2:])
    )


def _brief(task: str) -> str:
    # Edit tasks can already carry an older entry export. Quote the current
    # snapshot only and keep source JSON out of the instruction portion.
    for boundary in ("\nENTRY_SOURCE_JSON_BEGIN\n", "\nCURRENT_MANAGED_SOURCE_JSON\n"):
        before, separator, after = task.partition(boundary)
        if separator:
            _source, _end, remaining = after.partition("\nENTRY_SOURCE_JSON_END\n")
            task = before + ("\n" + remaining if remaining else "")
    return bounded_recovery_diagnostic(task, max_bytes=_MAX_BRIEF_BYTES)


def _source_context(files: Mapping[str, str], focus: Sequence[str]) -> str:
    excerpts: dict[str, dict[str, str | bool]] = {}
    for path in dict.fromkeys([*focus, _ENTRY, *sorted(files)]):
        source = files.get(path)
        if (source is None or path in MAX_MODEL_LOCKED_FILES
                or not path.startswith(("src/", "tests/", "drizzle/", "migrations/"))
                or not path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".sql", ".css"))
                or any(part.startswith(".") for part in path.split("/")) or "\\" in path
                or len(path) > 1024 or len(source) > 64 * 1024
                or contains_provider_secret(path) or redact_sensitive_text(path) != path
                or redact_sensitive_text(source) != source
                or max_model_write_rejection(path, source) is not None):
            continue
        try:
            encoded = source.encode("utf-8")
        except UnicodeError:
            continue
        incomplete = len(encoded) > _MAX_SNIPPET_BYTES
        if incomplete:
            source = encoded[:_MAX_SNIPPET_BYTES].decode("utf-8", errors="ignore")
        snippet: dict[str, str | bool] = {"source": source, "prefix_only": incomplete}
        proposed = json.dumps({**excerpts, path: snippet}, ensure_ascii=True)
        if len(proposed.encode("utf-8", errors="replace")) <= _MAX_SOURCE_BYTES:
            excerpts[path] = snippet
        if len(excerpts) == 6:
            break
    return json.dumps(excerpts, ensure_ascii=True)


def recovery_message(
    mode: NativeRecoveryMode, *, task: str, files: Mapping[str, str],
    focus: Sequence[str], objective: str, diagnostic: str | None,
) -> str:
    reason = {
        "inspection": "Repeated inspections of the same workspace did not advance the task.",
        "compiler": "The same compiler failure survived the previous source change.",
        "output_limit": "The model output was incomplete and no command from it was executed.",
        "transcript": "The gateway rejected the previous request before provider dispatch.",
    }[mode]
    feedback = bounded_recovery_diagnostic(diagnostic, max_bytes=2_000) if diagnostic else ""
    return (
        f"{reason}\n{objective}\n"
        "Earlier successful actions are already applied. Preserve saved source, working "
        "features, business data and applied migrations. Do not repeat prior effects.\n"
        f"\nOriginal task and constraints:\n{_brief(task)}\n"
        f"\nCurrent check or missing requirement (quoted data):\n{feedback or '(none)'}\n"
        f"\nSaved project source (JSON data; prefix_only means incomplete):\n"
        f"{_source_context(files, focus)}"
    )
