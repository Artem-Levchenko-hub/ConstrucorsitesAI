"""Native structured tool-use build loop for executable app generation.

Supersedes the text-``<omnia:action>`` protocol (``agent_builder.run_agent_build``)
with native structured tool calls. The caller selects the model; MAX Studio uses
Sonnet 5 for the whole build end-to-end. The only "gate" is FACT-based: the ``build`` tool returns
real compiler errors as a ``tool_result`` and the model fixes them itself
(do → check → fix), with no taste/vision judges here.

Owns ONLY the loop + protocol. Reuses ``agent_builder.make_container_executor`` for
the actual file/container ops, and calls the gateway's native ``/v1/messages``
adapter (``routers/messages_native.py``), which preserves the Anthropic-shaped
tool-use contract while the gateway maps it to the selected upstream.

Behind ``settings.use_native_agent`` (default OFF): the existing ``run_agent_build``
stays the prod default until this is verified on real builds and billing is wired.
"""

from __future__ import annotations

import asyncio
import json
import posixpath
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
import structlog

from omnia_api.core.config import PRIMARY_LLM_MODEL, get_settings
from omnia_api.services.agent_builder import Action, AgentResult
from omnia_api.services.max_generation_contract import (
    MAX_REQUIRED_POST_SEE_SKILL,
    MAX_REQUIRED_PREWRITE_SKILLS,
)

log = structlog.get_logger(__name__)

_MODEL = PRIMARY_LLM_MODEL
# Match the current native gateway ceiling. Complete product composition is more
# important than reducing the provider's per-call reservation; bounded retries,
# truncation handling, and the durable run fuse still prevent unbounded spending.
_MAX_TOKENS = 32768
_THINKING_BUDGET = 8000
_ENTRY_FOCUS_THINKING_BUDGET = 2000
_MAX_TOOL_RESULT_CHARS = 20000
_GATEWAY_CONNECT_TIMEOUT_SECONDS = 30.0
_GATEWAY_WRITE_TIMEOUT_SECONDS = 60.0
_GATEWAY_POOL_TIMEOUT_SECONDS = 30.0
_CALL_RETRIES = 1  # never duplicate a possibly-billed provider request inside one cycle
_MAX_PROVIDER_RECONNECT_CYCLES = 3
_MAX_PROVIDER_TIMEOUT_RESUMES = 2
_MAX_TRUNCATED_WRITE_ABORT_AT = 2
_STABLE_MAX_NOOP_WRITE_ABORT_AT = 2
_STABLE_MAX_PRODUCT_ENTRY = "src/components/product/ProductApp.tsx"
# A fresh build needs at most a design spec plus two compact domain/support files
# before composing the screen. A larger allowance was observed live producing
# competing types/catalog copies instead of the product entry.
_STABLE_MAX_SUPPORT_FILE_LIMIT = 3
_STABLE_MAX_PREWRITE_INSPECTION_LIMIT = 8
# One initial visual verdict plus two evidence-led repair passes is the paid QA
# ceiling. Live canaries showed that allowing eight passes can spend hundreds of
# roubles while a vision/model disagreement repeatedly redesigns the same screen.
# A third red verdict is preserved honestly for a later targeted edit instead of
# charging for five more speculative rewrites.
_STABLE_MAX_VISUAL_REPAIR_LIMIT = 2
_HISTORY_PLACEHOLDER_MARKERS = (
    "[OMITTED FROM HISTORY:",
    "[OLDER TOOL RESULT OMITTED:",
)
_NOOP_WRITE_REJECTED = (
    "The file is byte-identical after this edit, so it is not progress and must not be "
    "compiled again. Make one concrete source change that resolves the reported gap."
)


def _gateway_timeout() -> httpx.Timeout:
    """The API must outlive the gateway's complete error-classification path."""

    return httpx.Timeout(
        float(get_settings().native_gateway_read_timeout_seconds),
        connect=_GATEWAY_CONNECT_TIMEOUT_SECONDS,
        write=_GATEWAY_WRITE_TIMEOUT_SECONDS,
        pool=_GATEWAY_POOL_TIMEOUT_SECONDS,
    )


# EXPLORE-STALL guard — parity with run_agent_build's no_write_streak
# (agent_builder._NO_WRITE_NUDGE_AT/_NO_WRITE_ABORT_AT = 5/14, which count single
# text-protocol ACTIONS). Native counts assistant TURNS instead — one turn often
# bundles several parallel tool calls, and the native flow legitimately spends
# its first turns surveying the big template — so the nudge fires later (6 turns
# ≈ 8-15 read calls) and the abort at 12 turns still bounds a stalled build.
_NO_WRITE_NUDGE_AT = 6
_NO_WRITE_ABORT_AT = 12

# Infra circuit breaker: consecutive turns where EVERY executed tool op died on
# infra (container/orchestrator unreachable — executor tags obs["infra_dead"]).
# 3 turns tolerates a transient orchestrator restart. Generic builds still abort
# at that point; MAX can keep repairing while the gateway's durable financial
# fuse and the parent-task cancellation remain authoritative stop conditions.
_INFRA_DEAD_ABORT_AT = 3

# Native tool schemas — mirror the action set of make_container_executor._execute.
# `done` ends the loop. Kept intentionally minimal (fact tools only): the model
# decides everything else itself, like Claude Code.
_STR: dict[str, Any] = {"type": "string"}
_STR_ARRAY: dict[str, Any] = {"type": "array", "items": _STR}


def _tool(
    name: str, desc: str, props: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {"name": name, "description": desc, "input_schema": schema}


_TOOLS: list[dict[str, Any]] = [
    _tool("list_dir", "List a directory in the project.", {"path": _STR}),
    _tool("read_file", "Read a file's full contents.", {"path": _STR}, ["path"]),
    _tool("grep", "Regex-search files under a path.", {"pattern": _STR, "path": _STR}, ["pattern"]),
    _tool(
        "docs",
        "Fetch up-to-date external-library docs (Context7) so you use the "
        "REAL current API, not a stale/guessed one.",
        {"library": _STR, "query": _STR},
        ["library", "query"],
    ),
    _tool(
        "write_file",
        "Create or overwrite a whole file with its FULL content. Keep each file compact; "
        "if the product is large, split it into components. Across one assistant turn, "
        "keep all write/edit content below 24000 characters so tool arguments are not truncated.",
        {"path": _STR, "content": _STR},
        ["path", "content"],
    ),
    _tool(
        "edit_file",
        "Replace an exact, unique snippet inside a file.",
        {"path": _STR, "search": _STR, "replace": _STR},
        ["path", "search", "replace"],
    ),
    _tool(
        "build", "Typecheck/compile the app. Returns the real errors to fix (empty = clean).", {}
    ),
    _tool(
        "read_logs",
        "Tail the live dev-server logs (runtime errors build can't see).",
        {"tail": {"type": "integer"}},
    ),
    _tool(
        "runtime_check",
        "Open a route in the RUNNING app and get the REAL HTTP "
        "status — a typecheck-clean app can still 5xx on render.",
        {"path": _STR},
        ["path"],
    ),
    _tool(
        "see",
        "LOOK at a rendered route with your eyes: screenshots the running "
        "page (desktop + mobile) and returns a strict vision-designer critique — "
        "concrete fixes (hero too small, 3 identical cards, weak contrast, cramped "
        "spacing, generic look). A clean build does NOT mean it looks good; `see` "
        "is the only way to judge and fix TASTE. Default path '/'.",
        {"path": _STR},
    ),
    _tool(
        "generate_media",
        "GENERATE a real IMAGE or short VIDEO with AI (same key) "
        "and get back a hosted URL to EMBED (returned in the tool result — copy it "
        "into src). kind='image' (flux, ~5s — photoreal hero/section still). "
        "kind='video' (~1-3 min) — the SIGNATURE move is KEYFRAME "
        "INTERPOLATION: pass first_frame AND last_frame (each a vivid English scene "
        "prompt) and Flux paints both stills while the video model generates the UNIQUE "
        "motion BETWEEN them — a real fly-through ('летишь по острову при скролле'), not a "
        "generic loop. `prompt` = the MOTION/camera between the two frames. Each "
        "stage shows as its own live step. Optional: duration (3-10s), aspect "
        "('16:9'|'9:16'|'1:1'); first_frame_url/last_frame_url to reuse an already-"
        "made still instead of a prompt. Embed `<img src>` / `<video src autoPlay "
        "muted loop playsInline>` (or scroll-scrub currentTime). Video is SLOW + "
        "pricey (hard cap per build) — ONE key clip, reuse it, do NOT spam per-card.",
        {
            "kind": _STR,
            "prompt": _STR,
            "first_frame": _STR,
            "last_frame": _STR,
            "duration": {"type": "integer"},
            "aspect": _STR,
            "first_frame_url": _STR,
            "last_frame_url": _STR,
            "image_url": _STR,
        },
        ["kind", "prompt"],
    ),
    _tool(
        "probe",
        "Make a REAL request AS A LOGGED-IN test user and get the exact "
        "status+body — the only way to prove an interactive feature (create/"
        "save/submit) works end-to-end, which a clean build + 200 page do NOT.",
        {"method": _STR, "path": _STR, "body": {"type": "object"}},
        ["path"],
    ),
    _tool(
        "verify_isolation",
        "PROVE no cross-tenant leak: logs in TWO users, A "
        "creates the resource, then asserts B is DENIED reading it AND it is "
        "absent from B's list. Run for EVERY owned resource — a green build "
        "never proves isolation.",
        {"create": {"type": "object"}, "read": {"type": "object"}},
        ["create"],
    ),
    _tool(
        "plan_task",
        "Create or refine the observable execution plan. Store only objective, concrete "
        "steps and acceptance criteria — never hidden reasoning. Call once near the start "
        "of a substantial build; do not spend a separate turn on ceremonial planning.",
        {
            "objective": _STR,
            "steps": _STR_ARRAY,
            "acceptance_criteria": _STR_ARRAY,
        },
        ["objective", "steps", "acceptance_criteria"],
    ),
    _tool(
        "update_plan",
        "Persist a durable checkpoint after a meaningful milestone. Evidence must be an "
        "observable tool result (file, clean build, runtime status or visual verdict), not "
        "private reasoning.",
        {
            "step_id": _STR,
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "blocked"],
            },
            "summary": _STR,
            "evidence": _STR_ARRAY,
            "artifacts": _STR_ARRAY,
            "next_action": _STR,
        },
        ["step_id", "status", "summary"],
    ),
    _tool(
        "discover_capabilities",
        "Discover operator-approved read-only tools from real MCP servers. Use only when "
        "fresh external evidence can materially improve the current build; native file, "
        "build and visual tools remain the primary path.",
        {"server": _STR},
    ),
    _tool(
        "call_capability",
        "Call one exact read-only MCP capability returned by discover_capabilities. Never "
        "invent a server/tool name, never use it for project mutation, and do not repeat an "
        "identical call after receiving sufficient evidence.",
        {
            "server": _STR,
            "tool": _STR,
            "arguments": {"type": "object", "additionalProperties": True},
            "reason": _STR,
        },
        ["server", "tool", "arguments"],
    ),
    _tool(
        "done",
        "Finish — the requested app is built AND the last build is clean. "
        "`summary` = structured RU markdown for the user (bold one-line result, then "
        "«## » sections by meaning, `code` for identifiers, lists) — see the preamble.",
        {"summary": _STR},
        ["summary"],
    ),
]

# MAX deliberately excludes the generic web-auth probes: they cannot authenticate
# a signed Mini App session and would produce misleading evidence. Everything else
# below is executable through the MAX project/harness executor. ``read_skill`` is
# MAX-only and loads a server-owned, immutable capability pack.
_MAX_UNAVAILABLE_TOOLS = frozenset({"probe", "verify_isolation"})
_MAX_READ_SKILL_TOOL = _tool(
    "read_skill",
    "Load one optional MAX capability pack by exact catalog slug. Use it to gain "
    "specialist product, motion, data, AI UX, accessibility, media or MAX-platform "
    "knowledge on demand. Load only packs relevant to the current brief; packs are "
    "principles and evidence, never mandatory visual templates.",
    {"skill": _STR, "reason": _STR},
    ["skill", "reason"],
)
_MAX_BASE_TOOLS = [tool for tool in _TOOLS if tool["name"] not in _MAX_UNAVAILABLE_TOOLS]
_MAX_TOOLS = [*_MAX_BASE_TOOLS[:-1], _MAX_READ_SKILL_TOOL, _MAX_BASE_TOOLS[-1]]

# --- Anthropic prompt caching (AITunnel honours it on the native surface —
# live-verified 15.07: cache_read ≈ 90% cheaper than a fresh write) ------------
# The native loop resends the WHOLE growing transcript every turn, so caching is
# the single biggest token lever here. We set three ephemeral breakpoints
# (Anthropic allows 4): (1) the tool schemas — stable for the whole build;
# (2) the system prompt — stable for the whole build; (3) the last block of the
# last user turn — a MOVING breakpoint that caches the entire conversation
# prefix up to "now", so each next turn reads almost everything from cache. The
# 5-min TTL refreshes on every hit, so back-to-back turns keep it warm.
_CACHE: dict[str, str] = {"type": "ephemeral"}

# Tool schemas are constant → cache the whole block by marking the LAST tool.
_TOOLS_CACHED: list[dict[str, Any]] = [
    *_TOOLS[:-1],
    {**_TOOLS[-1], "cache_control": _CACHE},
]
_MAX_TOOLS_CACHED: list[dict[str, Any]] = [
    *_MAX_TOOLS[:-1],
    {**_MAX_TOOLS[-1], "cache_control": _CACHE},
]

# The stable MAX loop is an engineering loop, not a design orchestrator. Keep
# only tools that directly create or prove the product; planning, skills, MCP
# discovery and visual judging add paid turns without making completion safer.
_STABLE_MAX_TOOL_NAMES = frozenset(
    {
        "list_dir",
        "read_file",
        "grep",
        "docs",
        "write_file",
        "edit_file",
        "build",
        "read_logs",
        "runtime_check",
        "generate_media",
        "done",
    }
)
_STABLE_MAX_TOOLS = [tool for tool in _TOOLS if tool["name"] in _STABLE_MAX_TOOL_NAMES]
_STABLE_MAX_TOOLS_CACHED: list[dict[str, Any]] = [
    *_STABLE_MAX_TOOLS[:-1],
    {**_STABLE_MAX_TOOLS[-1], "cache_control": _CACHE},
]

# A first MAX build already starts from a clean, known starter. Four model turns
# are ample to inspect its small product surface. After that, expose only write
# tools until the first real product change lands. This turns the no-write nudge
# into an enforceable cost boundary instead of letting a model re-read the same
# files until the generic 12-turn explore guard aborts the generation.
_STABLE_MAX_FIRST_WRITE_AT = 4
# After two write-only turns, stop offering arbitrary support paths even when
# the file cap is not full. Live runs showed duplicate-support retries could
# otherwise continue indefinitely without composing the product entry.
_STABLE_MAX_ENTRY_FOCUS_AT = _STABLE_MAX_FIRST_WRITE_AT + 2
_STABLE_MAX_WRITE_REQUIRED = (
    "A product write is now required. Use write_file or edit_file; "
    "read/list/grep/build/done calls are disabled until one product file is written."
)
_STABLE_MAX_ENTRY_REQUIRED = (
    "The main product entry is still unchanged. Stay in write-only mode: create one compact "
    "required product component per turn (total tool content below 24000 characters), then "
    f"compose the complete screen in `{_STABLE_MAX_PRODUCT_ENTRY}`. Do not put the whole app "
    "in one oversized file. Notes, fake data, or decorative placeholders are not progress."
)
_STABLE_MAX_PROGRESS_REQUIRED = (
    "The product entry exists, but this build is not proven yet. Stop reading. Your next "
    "action must write/edit a required component or run build to expose concrete errors."
)
_STABLE_MAX_BUILD_REQUIRED = (
    "The product changed and must be compiled before any more rewriting. Run build now. "
    "If it is red, repair only the reported locations; if it is green, continue with "
    "runtime proof."
)
_STABLE_MAX_STYLE_REQUIRED = (
    "The product component exists, but its product-specific visual system is missing. "
    "Write the complete `src/app/globals.css` now. Preserve the Tailwind import, style "
    "the real component classes, mobile states and safe areas; do not rewrite ProductApp."
)
_STABLE_MAX_INSPECTION_COMPLETE = (
    "The MAX core has been inspected enough. Re-reading it will not improve the product. "
    "Write a product file now; compiler-guided repair will expose any remaining API mismatch."
)
_STABLE_MAX_SUPPORT_ADVANCE_REQUIRED = (
    "That supporting file is already written. Do not rewrite it before the product entry "
    f"exists. Create the next required component or compose `{_STABLE_MAX_PRODUCT_ENTRY}` now."
)
_STABLE_MAX_ENTRY_NOW_REQUIRED = (
    "The supporting-file budget is complete. Compose the real screen in "
    f"`{_STABLE_MAX_PRODUCT_ENTRY}` now; add or refine remaining components after that."
)
_HISTORY_PLACEHOLDER_WRITE_REJECTED = (
    "A transcript history placeholder is not source code and was not written. "
    "Read the target file again, then submit its actual complete source or a real exact edit."
)


def _stable_max_repair_required(paths: frozenset[str]) -> str:
    targets = ", ".join(f"`{path}`" for path in sorted(paths)) or "the file named by build"
    return (
        f"The build is RED. Fix {targets} now. Read each failing file at most once, then use "
        "edit_file for the smallest exact repair. write_file is disabled: never recreate the "
        "whole component during repair or reintroduce earlier errors. Do not read dependencies, "
        "rewrite unrelated files, or run build before a targeted edit. Preserve the product."
    )


_STABLE_MAX_REPAIR_VERIFY_REQUIRED = (
    "A targeted repair is applied. Keep the current files: write_file remains disabled. "
    "Use edit_file for any other already-reported failing location, or run build now."
)


def _stable_max_compact_repair_task(
    error: str,
    paths: frozenset[str],
    written: Mapping[str, str],
) -> str:
    sources: list[str] = []
    remaining = 24_000
    for path in sorted(paths):
        content = written.get(path, "")
        if not content or remaining <= 0:
            continue
        lines = content.splitlines()
        error_lines = sorted(
            {
                int(match.group("line"))
                for match in _TYPESCRIPT_ERROR_LOCATION_RE.finditer(error or "")
                if match.group("path") == path
            }
        )
        windows: list[tuple[int, int]] = []
        for line_number in error_lines:
            center = min(max(line_number - 1, 0), max(len(lines) - 1, 0))
            start = max(0, center - 8)
            end = min(len(lines), center + 9)
            if windows and start <= windows[-1][1] + 2:
                windows[-1] = (windows[-1][0], max(windows[-1][1], end))
            else:
                windows.append((start, end))
        if not windows:
            windows = [(0, min(len(lines), 240))]
        for start, end in windows:
            if remaining <= 0:
                break
            excerpt = "\n".join(lines[start:end])[:remaining]
            if not excerpt:
                continue
            remaining -= len(excerpt)
            sources.append(
                f"CURRENT `{path}` lines {start + 1}-{end} "
                "(exact source; preserve omitted code):\n"
                f"```tsx\n{excerpt}\n```"
            )
    return (
        "TARGETED COMPILER REPAIR. The current product is already implemented. "
        "Do not redesign or recreate files. Call edit_file only, replacing the smallest exact "
        "old_string that fixes all listed errors you can address in one edit. Source windows "
        "are exact but intentionally omit unrelated code; never include window labels in "
        "search.\n\n"
        f"BUILD ERRORS:\n{error[:12_000]}\n\n" + "\n\n".join(sources)
    )


_STABLE_MAX_FIRST_WRITE_TOOLS = [
    tool for tool in _STABLE_MAX_TOOLS if tool["name"] in {"write_file", "edit_file"}
]
_STABLE_MAX_FIRST_WRITE_TOOLS_CACHED: list[dict[str, Any]] = [
    *_STABLE_MAX_FIRST_WRITE_TOOLS[:-1],
    {**_STABLE_MAX_FIRST_WRITE_TOOLS[-1], "cache_control": _CACHE},
]
_STABLE_MAX_VISUAL_REPAIR_TOOLS = [
    tool
    for tool in _STABLE_MAX_TOOLS
    if tool["name"] in {"write_file", "edit_file", "generate_media"}
]
_STABLE_MAX_VISUAL_REPAIR_TOOLS_CACHED: list[dict[str, Any]] = [
    *_STABLE_MAX_VISUAL_REPAIR_TOOLS[:-1],
    {**_STABLE_MAX_VISUAL_REPAIR_TOOLS[-1], "cache_control": _CACHE},
]
_STABLE_MAX_REPAIR_TOOLS = [
    tool for tool in _STABLE_MAX_TOOLS if tool["name"] in {"read_file", "edit_file"}
]
_STABLE_MAX_REPAIR_TOOLS_CACHED: list[dict[str, Any]] = [
    *_STABLE_MAX_REPAIR_TOOLS[:-1],
    {**_STABLE_MAX_REPAIR_TOOLS[-1], "cache_control": _CACHE},
]
_STABLE_MAX_REPAIR_EDIT_ONLY_TOOLS_CACHED: list[dict[str, Any]] = [
    {
        **next(tool for tool in _STABLE_MAX_TOOLS if tool["name"] == "edit_file"),
        "cache_control": _CACHE,
    }
]
_STABLE_MAX_REPAIR_VERIFY_TOOLS = [
    tool for tool in _STABLE_MAX_TOOLS if tool["name"] in {"read_file", "edit_file", "build"}
]
_STABLE_MAX_REPAIR_VERIFY_TOOLS_CACHED: list[dict[str, Any]] = [
    *_STABLE_MAX_REPAIR_VERIFY_TOOLS[:-1],
    {**_STABLE_MAX_REPAIR_VERIFY_TOOLS[-1], "cache_control": _CACHE},
]
_STABLE_MAX_PROGRESS_TOOLS = [
    tool for tool in _STABLE_MAX_TOOLS if tool["name"] in {"write_file", "edit_file", "build"}
]
_STABLE_MAX_PROGRESS_TOOLS_CACHED: list[dict[str, Any]] = [
    *_STABLE_MAX_PROGRESS_TOOLS[:-1],
    {**_STABLE_MAX_PROGRESS_TOOLS[-1], "cache_control": _CACHE},
]
_STABLE_MAX_BUILD_ONLY_TOOLS_CACHED: list[dict[str, Any]] = [
    {
        **next(tool for tool in _STABLE_MAX_TOOLS if tool["name"] == "build"),
        "cache_control": _CACHE,
    }
]
_STABLE_MAX_STYLE_ONLY_TOOLS_CACHED: list[dict[str, Any]] = [
    {
        **_tool(
            "write_file",
            "Write the complete product-specific visual system.",
            {
                "path": {"type": "string", "enum": ["src/app/globals.css"]},
                "content": _STR,
            },
            ["path", "content"],
        ),
        "cache_control": _CACHE,
    }
]
_STABLE_MAX_SOURCE_REPAIR_REQUIRED = (
    "A concrete source-contract gap remains after a clean build. Stop reading or "
    "polishing unrelated UI. Write or edit the current product now to fix exactly "
    "the stated gap, then rebuild."
)
_STABLE_MAX_VISUAL_FINISH_TOOLS_CACHED: list[dict[str, Any]] = [
    _tool(
        "edit_file",
        "Apply the remaining exact visual repair to the product stylesheet.",
        {
            "path": {"type": "string", "enum": ["src/app/globals.css"]},
            "search": _STR,
            "replace": _STR,
        },
        ["path", "search", "replace"],
    ),
    {
        **next(tool for tool in _STABLE_MAX_TOOLS if tool["name"] == "build"),
        "cache_control": _CACHE,
    },
]
_STABLE_MAX_PROOF_TOOLS = [tool for tool in _STABLE_MAX_TOOLS if tool["name"] == "runtime_check"]
_STABLE_MAX_PROOF_TOOLS_CACHED: list[dict[str, Any]] = [
    *_STABLE_MAX_PROOF_TOOLS[:-1],
    {**_STABLE_MAX_PROOF_TOOLS[-1], "cache_control": _CACHE},
]
_STABLE_MAX_RUNTIME_ONLY_TOOLS_CACHED: list[dict[str, Any]] = [
    {
        **next(tool for tool in _STABLE_MAX_TOOLS if tool["name"] == "runtime_check"),
        "cache_control": _CACHE,
    }
]
_STABLE_MAX_PROOF_REQUIRED = (
    "The product source and build are green. Stop reading or polishing blindly. "
    "Run runtime_check now; if it reports a concrete issue, edit it on the following "
    "turn, rebuild, and verify again."
)
_STABLE_MAX_RUNTIME_PROOF_REQUIRED = (
    "The build is green but the final runtime is not proven. Run runtime_check now; "
    "do not read, rewrite, or call see before the live route is green."
)
_STABLE_MAX_VISUAL_REPAIR_REQUIRED = (
    "A concrete visual issue is already known. Stop searching or rereading. "
    "Write or edit the product now to apply that visual feedback. If the verdict "
    "specifically requires real imagery, generate one suitable image first and embed "
    "its returned hosted URL on the next turn. Then rebuild and verify the render again."
)
_STABLE_MAX_VISUAL_FINISH_REQUIRED = (
    "The component-side visual repair is applied. Before proof, either edit "
    "`src/app/globals.css` once to finish the exact CSS/layout feedback or run "
    "build now if no stylesheet change is needed. Do not rewrite ProductApp, "
    "read files, or skip directly to runtime/see."
)
_STABLE_MAX_ENTRY_ONLY_TOOLS_CACHED = [
    {
        **_tool(
            "write_file",
            f"Write the complete compact product composition to {_STABLE_MAX_PRODUCT_ENTRY}.",
            {
                "path": {"type": "string", "enum": [_STABLE_MAX_PRODUCT_ENTRY]},
                "content": _STR,
            },
            ["path", "content"],
        ),
        "cache_control": _CACHE,
    }
]

# The reliable MAX path from 4cb0ee18 was a single Google tool loop, not a
# ceremony of nested planners, capability brokers and mandatory visual judges.
# Keep only the tools that can directly advance or prove the product.  This also
# shrinks every provider request and leaves more of Gemini's context for the app.
_MAX_REFERENCE_TOOL_NAMES = frozenset(
    {
        "list_dir",
        "read_file",
        "grep",
        "docs",
        "write_file",
        "edit_file",
        "build",
        "read_logs",
        "runtime_check",
        "see",
        "generate_media",
        "done",
    }
)
_MAX_REFERENCE_TOOLS = [
    tool for tool in _TOOLS if str(tool.get("name") or "") in _MAX_REFERENCE_TOOL_NAMES
]
_MAX_REFERENCE_TOOLS_CACHED: list[dict[str, Any]] = [
    *_MAX_REFERENCE_TOOLS[:-1],
    {**_MAX_REFERENCE_TOOLS[-1], "cache_control": _CACHE},
]


def _system_blocks(system: str) -> list[dict[str, Any]]:
    """System prompt as a single cache-marked text block (Anthropic's `system`
    accepts a block list; cache_control can't ride a plain string)."""
    return [{"type": "text", "text": system, "cache_control": _CACHE}]


def _with_incremental_cache(convo: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``convo`` with a moving cache breakpoint on the last block of the
    last user turn — caches the whole prefix so the next turn reads it back
    instead of re-billing it. The original list is NOT mutated (assistant turns,
    incl. thinking-block signatures, must be echoed verbatim); only the tail
    message is shallow-copied. No-op unless the last turn is a user turn (always
    true at call time: task, then tool_result batches)."""
    if not convo or convo[-1].get("role") != "user":
        return convo
    last = convo[-1]
    content = last.get("content")
    if isinstance(content, str) and content:
        new_content: list[dict[str, Any]] = [
            {"type": "text", "text": content, "cache_control": _CACHE}
        ]
    elif isinstance(content, list) and content:
        new_content = list(content)
        new_content[-1] = {**new_content[-1], "cache_control": _CACHE}
    else:
        return convo
    return [*convo[:-1], {**last, "content": new_content}]


def _tool_use_to_action(block: dict[str, Any]) -> Action:
    inp = block.get("input") or {}
    if not isinstance(inp, dict):
        inp = {}
    return Action(name=str(block.get("name", "")), args=dict(inp), raw="")


def _normalize_stable_max_action_path(action: Action) -> Action:
    """Repair one harmless provider path spelling before strict policy checks.

    Some coding models occasionally prefix a project-relative MAX path with one
    slash (``/src/...``). Only known project roots are repaired; arbitrary
    absolute paths, duplicate slashes, traversal and backslashes remain intact
    and are rejected by ``max_model_path_rejection`` in the executor.
    """

    path = action.path
    if action.name not in {"list_dir", "read_file", "grep", "write_file", "edit_file"}:
        return action
    if not path.startswith(("/src/", "/public/product/", "/.omnia/")):
        return action
    return Action(
        name=action.name,
        args={**action.args, "path": path[1:]},
        raw=action.raw,
    )


def _contains_history_placeholder(action: Action) -> bool:
    if action.name not in {"write_file", "edit_file"}:
        return False
    return any(
        marker in value
        for value in action.args.values()
        if isinstance(value, str)
        for marker in _HISTORY_PLACEHOLDER_MARKERS
    )


def _obs_to_tool_result(
    tool_use_id: str,
    obs: dict[str, Any],
    *,
    tool_name: str = "",
) -> dict[str, Any]:
    ok = bool(obs.get("ok"))
    # The executor returns the whole post-edit file in ``content`` so the API can
    # track the exact snapshot. Echoing that same 20–40 KB file back to the model
    # is both unnecessary (the file is already in the container) and extremely
    # expensive: every later turn resends it in the conversation. Keep the
    # immediate observation factual but compact; the model can call read_file if
    # it genuinely needs to inspect the resulting source again.
    if ok and tool_name in {"write_file", "edit_file"}:
        content = obs.get("content")
        size = len(content) if isinstance(content, str) else None
        verb = "written" if tool_name == "write_file" else "edited"
        body = (
            f"File {verb} successfully"
            + (f" ({size} characters)" if size is not None else "")
            + ". Current source is in the container."
        )
    else:
        body = (
            obs.get("content") or obs.get("detail") or obs.get("error") or ("ok" if ok else "error")
        )
    text = str(body)[: _MAX_TOOL_RESULT_CHARS // 2]
    status = str(obs.get("status") or ("success" if ok else "error"))
    raw_next_actions = obs.get("next_actions")
    next_actions = (
        [str(item)[:500] for item in raw_next_actions[:6]]
        if isinstance(raw_next_actions, list)
        else []
    )
    if not next_actions:
        if ok and tool_name in {"write_file", "edit_file"}:
            next_actions = ["Run build and fix the exact compiler output if it fails."]
        elif ok and tool_name == "build":
            next_actions = ["Run the remaining runtime and visual proof after the last write."]
        elif not ok:
            next_actions = [
                "Use the root-cause hint once; stop repeating the identical failing call."
            ]
    raw_artifacts = obs.get("artifacts")
    artifacts = (
        [str(item)[:500] for item in raw_artifacts[:20]] if isinstance(raw_artifacts, list) else []
    )
    if tool_name in {"write_file", "edit_file"} and ok:
        artifacts = list(dict.fromkeys([*artifacts, str(obs.get("path") or "")]))
        artifacts = [item for item in artifacts if item]
    payload = {
        "status": status,
        "summary": str(obs.get("summary") or text)[:1000],
        "next_actions": next_actions,
        "artifacts": artifacts,
        "data": "" if ok and tool_name in {"write_file", "edit_file"} else text,
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": serialized,
    }
    if not ok:
        block["is_error"] = True
    return block


# A `build` failure that references a non-existent internal module (TS2307) is a
# specific, self-inflicted failure mode: the model hallucinates a data-access
# "SDK"/"engine" layer (`@/lib/entities/engine`, `@/lib/sdk/*`) that belongs to a
# DIFFERENT stack and doesn't exist here → the whole build stays red and the loop
# burns steps re-reading. Detect it and hand the model the CORRECT recovery so it
# fixes the build in its own loop instead of scaffolding the phantom module.
_TS2307_RE = re.compile(r"Cannot find module '(@/[^']+)'")


def _module_not_found_hint(build_output: str) -> str | None:
    """If a build error is `TS2307: Cannot find module '@/...'`, return an inline
    hint steering the model to delete the phantom import / use the real primitive
    (never to scaffold the missing module). None if no such error present."""
    mods = _TS2307_RE.findall(build_output or "")
    if not mods:
        return None
    uniq = list(dict.fromkeys(mods))[:5]
    return (
        "\n\n[HINT] These imports point at modules that DO NOT EXIST in this "
        f"project: {', '.join(uniq)}. Do NOT create them and do NOT build an "
        "SDK/engine/repository wrapper to satisfy the import — that pattern is from "
        "a different stack. Remove the phantom import and use the real primitive "
        "your stack guide documents (query the DB directly), or inline the logic. "
        "Verify a path with list_dir/grep before importing it."
    )


# Next.js App Router: a route group `(name)` does NOT affect the URL, so
# `app/(app)/login/page.tsx` and `app/login/page.tsx` BOTH resolve to `/login`
# and the build dies with "two parallel pages that resolve to the same path".
# A weak model hits this on a restyle/translate turn by creating a second
# `page.tsx` instead of editing the existing one (observed live 2026-07-16 on the
# «переведи на русский» edit — auto-repair fixed it but burned 15 steps cycling
# on write_file because it didn't know to remove the duplicate). Hand the model
# the exact recovery so it fixes in ~2 steps.
_PARALLEL_PAGES_RE = re.compile(
    r"two parallel pages that resolve to the same path.*?check\s+(\S+)\s+and\s+(\S+)",
    re.IGNORECASE | re.DOTALL,
)

_TYPESCRIPT_ERROR_PATH_RE = re.compile(r"(?m)^((?:src|app|pages)/.+?)\(\d+,\d+\):\s+error\s+TS\d+")
_TYPESCRIPT_ERROR_LOCATION_RE = re.compile(
    r"(?m)^(?P<path>(?:src|app|pages)/.+?)\((?P<line>\d+),\d+\):\s+error\s+TS\d+"
)
_TYPESCRIPT_RELATIVE_MODULE_RE = re.compile(
    r"""(?m)^(?P<source>(?:src|app|pages)/.+?)\(\d+,\d+\):\s+error\s+TS\d+:[^\n]*?"""
    r"""Module\s+["']+(?P<module>\.{1,2}/[^"']+)["']+"""
)


def _typescript_error_paths(build_output: str) -> frozenset[str]:
    """Files named by TypeScript diagnostics in a build result."""

    return frozenset(_TYPESCRIPT_ERROR_PATH_RE.findall(build_output or ""))


def _typescript_repair_paths(
    build_output: str,
    written: Mapping[str, str],
) -> frozenset[str]:
    """Diagnostic files plus local modules explicitly named by TypeScript.

    A TS2305/TS2307 reported in ``catalog.ts`` is often fixed in its imported
    ``types.ts`` contract. Restricting repair edits to the diagnostic file makes
    that valid compiler-guided repair impossible and leaves the model cycling on
    reads/builds. Only already-present relative source modules are admitted.
    """

    paths = set(_typescript_error_paths(build_output))
    for match in _TYPESCRIPT_RELATIVE_MODULE_RE.finditer(build_output or ""):
        source = str(match.group("source") or "")
        module = str(match.group("module") or "")
        base = posixpath.normpath(posixpath.join(posixpath.dirname(source), module))
        candidates = (
            base,
            f"{base}.ts",
            f"{base}.tsx",
            f"{base}/index.ts",
            f"{base}/index.tsx",
        )
        dependency = next((candidate for candidate in candidates if candidate in written), None)
        if dependency:
            paths.add(dependency)
    return frozenset(paths)


def _parallel_pages_hint(build_output: str) -> str | None:
    """If the build error is Next.js's "two parallel pages" conflict, return an
    inline hint steering the model to remove the duplicate route (keep one), not
    to create more files. None if no such error present."""
    m = _PARALLEL_PAGES_RE.search(build_output or "")
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    return (
        f"\n\n[HINT] {a} and {b} both resolve to the SAME URL — a route group "
        "`(name)` does not change the path, so two page.tsx at that path collide. "
        "Do NOT create another file and do NOT keep rewriting the same page. Keep "
        "ONE canonical route and neutralize the duplicate: overwrite the extra "
        "page.tsx to re-export the survivor (`export { default } from '<path>'`) "
        "or replace it with a redirect, and repoint links. Use list_dir to see "
        "both before acting."
    )


def _build_error_hint(build_output: str) -> str:
    """Concatenate every deterministic recovery hint that applies to this build
    error (empty string if none). Keeps the fact-loop steering in one place."""
    return "".join(
        h
        for h in (
            _module_not_found_hint(build_output),
            _parallel_pages_hint(build_output),
        )
        if h
    )


def _text_of(content: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


# Cap the per-step detail so drilling into a step stays cheap on the WS + UI.
_STEP_DETAIL_CAP = 1400


def _step_detail(name: str, action: Action, obs: dict[str, Any]) -> str:
    """A short, human-inspectable preview of what a tool step DID — shown when the
    user drills into the step. Empty string when there's nothing useful to show."""

    def _cap(s: Any) -> str:
        t = str(s or "")
        return t if len(t) <= _STEP_DETAIL_CAP else t[:_STEP_DETAIL_CAP] + "\n… (обрезано)"

    if not obs.get("ok", True):
        return _cap(obs.get("error") or obs.get("detail") or "ошибка")
    if name == "write_file":
        content = str(action.args.get("content", "") or "")
        return _cap(f"{len(content)} символов записано:\n\n{content}")
    if name == "edit_file":
        return _cap(obs.get("content") or obs.get("detail") or "правка применена")
    if name == "read_file":
        return _cap(obs.get("content") or "")
    if name == "build":
        return _cap(obs.get("detail") or obs.get("content") or "сборка чистая")
    if name in ("grep", "list_dir", "read_logs", "docs"):
        return _cap(obs.get("detail") or obs.get("content") or "")
    if name in ("runtime_check", "probe", "verify_isolation"):
        return _cap(obs.get("detail") or obs.get("content") or "проверка пройдена")
    return _cap(obs.get("detail") or obs.get("content") or "")


_NATIVE_PREAMBLE = (
    "Ты — автономный инженер: строишь РАБОЧЕЕ приложение в этом проекте, как Claude "
    "Code. Инструменты вызывай напрямую: read_file/list_dir/grep — понять код, "
    "write_file/edit_file — писать, build — компиляция, read_logs — рантайм, "
    "runtime_check — открыть роут в ЖИВОМ приложении, probe — реальный запрос ОТ "
    "ИМЕНИ залогиненного юзера, verify_isolation — доказать отсутствие утечки данных "
    "между юзерами, docs — свежая дока библиотек. Думай сколько нужно. Цикл: пиши "
    "код → build → чини РЕАЛЬНЫЕ ошибки до чистоты → ДОКАЖИ что работает → done. Пиши "
    "полноценно, без заглушек и TODO. Для большой задачи вызови plan_task один раз "
    "вместе с первыми полезными действиями, затем update_plan только после реальных "
    "milestones; это наблюдаемый checkpoint, не пересказ скрытых рассуждений. Внешние "
    "MCP-возможности сначала найди через discover_capabilities и вызывай только когда "
    "они дают необходимые свежие факты.\n\n"
    "ДОКАЖИ перед done — чистый build это НЕ доказательство работы: "
    "(1) runtime_check главные роуты (чистый typecheck всё равно может 5xx на рендере); "
    "(2) для интерактива (создать/сохранить/отправить/удалить) — probe РЕАЛЬНЫМ запросом "
    "от залогиненного юзера, требуй 2xx с ожидаемым телом (чистая страница НЕ доказывает, "
    "что POST/DELETE юзера не отдаёт 4xx); (3) для данных юзера — verify_isolation на "
    "КАЖДОМ владеемом ресурсе (green build не доказывает изоляцию). Чини до зелёного — "
    "потом done.\n\n"
    "ВАЖНО: если build пишет `Cannot find module '@/...'` — этого пути НЕТ в проекте. "
    "НЕ создавай модуль под импорт и НЕ выдумывай SDK/engine/repository-обёртку; удали "
    "фантомный импорт и используй реальный примитив стека (см. гайд) напрямую. "
    "И НИКОГДА не делай fetch() к СВОЕМУ ЖЕ API из серверного кода (server component / "
    "server action / route handler) — cookie сессии не передаётся (будет 401) и это "
    "лишний круг; вызывай `db`/данные напрямую в самой функции.\n\n"
    "ВКУС В ДИЗАЙНЕ — чистый build ≠ красиво. Перед done ОБЯЗАТЕЛЬНО `see` главный "
    "экран (и ещё 1 ключевой, если есть) — vision-судья вернёт КОНКРЕТНЫЕ фиксы; "
    "примени их и повтори `see`, пока не станет чисто. `see` дорог — 1–2 ключевых "
    "экрана, НЕ каждый. Принципы (это НЕ шаблон — думай под нишу): (1) иерархия — "
    "ОДИН доминантный герой/заголовок, вторичное тише; (2) контраст ≥ 4.5:1; "
    "(3) ритм отступов кратен 4/8, секции просторные, воздух; (4) тип-шкала "
    "(крупный герой → мельче тело), не один размер; (5) НИКОГДА «голый Tailwind» "
    "дефолт (сине-серый, одинаковые карточки) — один бренд-акцент дозой; "
    "(6) mobile-first (адаптив — жёсткое условие). Не «сделай красивее» вслепую — "
    "`see` → конкретный дефект → точечный фикс.\n\n"
    "ОРКЕСТРАЦИЯ МОДЕЛЕЙ ИЗ ОБЫЧНОГО ПРОМПТА — пользователь пишет ЖИВЫМ языком "
    "(«сайт про остров, чтобы при скролле будто летишь над ним», «оживи», «вау», "
    "«кинематографично», «3D»), НЕ называя моделей. ТЫ дирижёр: сам прочитай "
    "замысел → построй цепочку моделей → покажи этапы. Как читать намерение → план:\n"
    "• «полёт/пролёт/погружение/кино/3D/сторителлинг при скролле» → Flux рисует "
    "2 ключевых кадра (старт сцены + финал) ⇒ видео-модель соединяет их в УНИКАЛЬНЫЙ пролёт "
    "(интерполяция) ⇒ фронт крепит скролл-скрабом (`currentTime` от прогресса) — "
    "картинка «летит» при прокрутке. Один `generate_media(kind='video', first_frame, "
    "last_frame, prompt=движение)` запускает всю цепочку.\n"
    "• «фотореалистичный герой/секция, атмосфера» → Flux-картинка (kind='image').\n"
    "• обычный контентный сайт → без видео, но ВСЕГДА живые hover/reveal (см. ниже).\n"
    "Модели ВЗАИМОДЕЙСТВУЮТ так: Flux (кадры-стоп-кадры) → видео-модель (движение МЕЖДУ "
    "кадрами) → фронт (скролл/hover-оживление). В финальном ответе (done) КОРОТКО "
    "объясни пользователю, какая связка сработала и почему — чтобы он видел замысел.\n\n"
    "МЕДИА (картинки + КИНО-ВИДЕО) — у тебя есть `generate_media`, тот же ключ, "
    "возвращает готовый URL (он приходит в результате тула — ВСТАВЬ его в src). "
    "kind='image' — фото-герой/секции (flux). kind='video' — коронный приём "
    "КЕЙФРЕЙМ-ИНТЕРПОЛЯЦИЯ: передай first_frame И last_frame (промпт каждой сцены) — "
    "Flux нарисует ОБА кадра (первый и последний), а видео-модель сделает УНИКАЛЬНЫЙ плавный "
    "переход-пролёт между ними (`prompt` = движение/камера между кадрами). Это и есть "
    "«летишь по острову при скролле»: first_frame='аэросъёмка края острова, рассвет', "
    "last_frame='камера у вулкана крупным планом, золотой свет', prompt='плавный "
    "облёт вперёд над джунглями'. Каждый этап (первый кадр → последний кадр → склейка "
    "видео-модель) виден пользователю отдельным шагом. Встраивание: (a) фоновый луп — "
    "`<video autoPlay muted loop playsInline>` в `absolute inset-0 object-cover -z-10`, "
    "контент поверх; (b) скролл-скраб «летишь при скролле» — sticky-контейнер на "
    '100–300vh, `video.currentTime` привязан к прогрессу скролла (`preload="metadata"` '
    "`muted`). Всегда `poster=` + градиент-оверлей для читаемости текста. Видео МЕДЛЕННОЕ "
    "и дорогое (жёсткий лимит клипов на сборку) — 1 ключевой клип, переиспользуй, НЕ по "
    "клипу на карточку. Не медиа ради медиа — только когда усиливает смысл ниши.\n\n"
    "ПЛАВНОСТЬ (60fps, ноль лагов на скролле — ЖЁСТКОЕ ПРАВИЛО) — скролл-скраб видео "
    "лагает, если делать наивно. Клип уже приходит оптимизированным (all-keyframe + "
    "faststart, seek мгновенный), но КОД скролла обязан быть лёгким: (1) НИКОГДА не "
    "пиши `currentTime`/не читай layout прямо в `onScroll` — только внутри "
    "requestAnimationFrame, с флагом `ticking`, и пропускай кадр если прогресс изменился "
    "на <0.003 или |currentTime−target|<0.03 (микро-seek'и = джанк); (2) для чистого фона "
    "БЕЗ сюжета бери `autoPlay muted loop` (композитор GPU, 0 нагрузки на main-thread) — "
    "скраб только когда «полёт» реально привязан к сюжету; (3) GPU-композит: анимируй "
    "ТОЛЬКО `transform`/`opacity` (+`will-change`,`translateZ(0)`), никогда top/left/"
    "width/height и не тяжёлый `backdrop-blur` на большой скролл-зоне; (4) `IntersectionObserver`, "
    'не scroll-математика, для появления секций; (5) картинки — `loading="lazy" decoding="async"`, '
    "ширина под контейнер (не 4K-PNG в карточку 400px); (6) один тяжёлый клип на страницу.\n\n"
    "ЖИВЫЕ МИКРО-ВЗАИМОДЕЙСТВИЯ (hover/скролл) — статичная страница мертва; оживляй "
    "точечно на наведение и появление. Приёмы: карточка на hover — лёгкий подъём "
    "`-translate-y` + тень + картинка внутри чуть увеличивается (Ken Burns, "
    "`scale-105 transition-transform duration-500`, обёртка `overflow-hidden group`, "
    "картинка `group-hover:scale-105`); по дорожке/пути/линии — ПОДСВЕТКА на hover "
    "(SVG `stroke-dashoffset` анимация, или бегущий градиент); персонаж/иконка — "
    "микро-движение на `group-hover` (`transition-transform`, кадр-луп); появление "
    "секций при скролле — мягкий fade/slide через IntersectionObserver (НЕ прячь "
    "контент до JS — стартовое состояние видимо, анимация усиливает). Тонко и "
    "целенаправленно (`transition`, `will-change`, `duration-300..700`, `ease-out`), "
    "не мигать всем сразу; уважай `prefers-reduced-motion`.\n\n"
    "МЕНЬШЕ БАГОВ, БЫСТРЕЕ: перед нетривиальным фиксом ДУМАЙ root-cause (не патч "
    "наугад — это плодит новые баги). Не изобретай API/SDK — `docs` (Context7) даёт "
    "РЕАЛЬНУЮ текущую сигнатуру (галлюцинация API = главный источник цикла build↔fix). "
    "Пойми минимально (read/grep) → пиши ПОЛНЫМИ файлами → build → чини реальные "
    "ошибки → ДОКАЖИ (runtime_check/probe/verify) → `see` дизайн → done. Не крути "
    "лишние read, когда контекста хватает.\n\n"
    "ФИНАЛЬНЫЙ ОТВЕТ (аргумент summary в done) — это markdown, его показывают "
    "пользователю С ФОРМАТИРОВАНИЕМ. Оформи СТРУКТУРНО по СМЫСЛУ, не сплошным текстом:\n"
    "• Первая строка — ИТОГ одним предложением, ключевой результат выдели "
    "**жирным**. Без заголовка над ней.\n"
    "• Дальше — секции с заголовками «## » ПО СМЫСЛУ (бери только нужные, обычно 2–4): "
    "что сделал · как это работает · что проверил · что дальше.\n"
    "• `бэктики` — на КАЖДЫЙ идентификатор: имена файлов, функций, флагов, роутов, команд, полей.\n"
    "• **жирным** — ключевые фичи и сущности; *курсивом* — нюанс, оговорку, «почему так».\n"
    "• Списки «- » — для перечислений (что изменилось, шаги, проверки).\n"
    "• Простыми словами, без канцелярита и без «я выполнил задачу»; технический термин — "
    "с коротким пояснением в скобках. По делу и развёрнуто (что сделал → зачем → эффект), без воды."
)

_MAX_NATIVE_PREAMBLE = (
    "MAX PRODUCT STUDIO — ты автономная senior-команда: продуктовый дизайнер, "
    "motion-дизайнер и инженер MAX Mini Apps в одном агенте. Твоя цель — не просто "
    "зелёная сборка, а цельный production-grade мобильный продукт с характером, "
    "реальными сценариями и профессиональной детализацией. Инструменты вызывай "
    "напрямую: read_file/list_dir/grep — понять защищённое ядро, write_file/edit_file — "
    "писать продуктовые файлы, build — компиляция, runtime_check — живой роут, see — "
    "скриншоты и независимая mobile/MAX-критика. Пиши полноценно, без TODO, заглушек, "
    "декоративных кнопок и симулированного успеха. В начале существенной сборки уточни "
    "наблюдаемый план через plan_task, затем фиксируй реальные milestones инструментом "
    "update_plan. Не записывай скрытые рассуждения. Для свежей внешней документации и "
    "исследований доступны разрешённые read-only MCP capabilities: сначала "
    "discover_capabilities, затем один точный call_capability; не подменяй ими работу "
    "с живым проектом и не зацикливайся на внешнем сервере.\n\n"
    "АРТ-ДИРЕКЦИЯ ДО КОДА. Внутренне сформируй ТРИ действительно разных направления "
    "для этого брифа — они должны различаться композицией, плотностью, типографическим "
    "голосом, формой и хореографией движения, а не только цветом. Выбери одно по "
    "соответствию аудитории и главному действию. Зафиксируй для себя product promise, "
    "информационную иерархию, экраны/состояния, визуальную систему и motion language, "
    "запиши выбранную систему в `.omnia/max-design-spec.json` по acceptance contract и "
    "после этого последовательно реализуй концепцию. Никогда не воспроизводи "
    "универсальный dashboard, прошлую генерацию или маркетинговый лендинг.\n\n"
    "ВИЗУАЛЬНАЯ СВОБОДА БЕЗ ШАБЛОНА. Ты владеешь "
    "src/components/product/ProductApp.tsx, src/app/globals.css и новыми клиентскими "
    "продуктовыми компонентами. globals.css можно и нужно "
    'полностью оформить под концепцию, но сохрани корректный `@import "tailwindcss"` '
    "и располагай внешние font-import ДО него. Это обычный глобальный CSS, не CSS Module: "
    "никогда не используй в нём `:global(...)`. Не трогай locked layout/provider/runtime. "
    "Определи собственные семантические CSS variables (`--app-*`) и используй их через "
    "обычный CSS или Tailwind arbitrary values; не вызывай несуществующие "
    "`bg-background`/`border-border` без явного mapping. Один доминирующий акцент, "
    "выразительная типографическая шкала, осмысленные поверхности и ритм важнее радуги, "
    "градиентов и множества одинаковых карточек.\n\n"
    "МОБИЛЬНЫЙ ПРОДУКТ, НЕ САЙТ. Проектируй сначала для 360–390px: главное действие "
    "видно сразу, навигация не перекрывает контент, safe-area учтён, tap targets удобны "
    "для пальца, данные читаются без горизонтального скролла. Используй реальный профиль "
    "MAX и Bridge там, где это улучшает сценарий. Loading, empty, error/retry, success, "
    "selected/pressed/disabled — полноценные состояния, а не подписи в макете. Не ставь "
    "fixed/sticky CTA поверх прокручиваемых контролов: оставляй действие в потоке либо "
    "выделяй ему собственную непрозрачную область и реальный spacer. Если вариантов больше "
    "трёх, используй компактные chips/segmented/grid или progressive disclosure, чтобы до "
    "главного действия не стояла длинная колонка однотипных карточек.\n\n"
    "AI-РЕЗУЛЬТАТ — ЭТО ЭКРАН, НЕ СЫРОЙ ТЕКСТ. Проси у managed AI короткий, "
    "структурированный ответ и показывай его секциями, шагами или списком с ясной "
    "иерархией. Никогда не выводи длинный `answer` одним сплошным абзацем в общей "
    "карточке; на 360px сохрани читаемую длину строки и видимое следующее действие.\n\n"
    "FIRST-RUN БЕЗ ПУСТОТЫ И ФАЛЬШИ. Честное отсутствие истории не означает пустой экран: "
    "первый viewport должен содержать обещание продукта, одно главное решение/действие и "
    "полезный следующий слой из брифа (например, выбор цели, каталог или объяснение процесса), "
    "но не выдуманные достижения. Не растягивай блоки через space-between/min-height так, чтобы "
    "между ними возникали огромные провалы. При nullable MAX-профиле никогда не показывай имя-"
    "заглушку «Пользователь», «User» или «Гость»: используй нейтральную фразу без выдуманного "
    "имени. Loading обязан сохранять ту же брендовую оболочку и геометрию, а 360px и 390px "
    "после settle должны показывать один и тот же продукт, не разные splash/content экраны.\n\n"
    "ЖИВОЕ ДВИЖЕНИЕ. Добавляй короткие целевые micro-interactions: press feedback, "
    "переключение сегментов, изменение progress/counter, появление и удаление строки, "
    "skeleton→content, bottom sheet, подтверждение успеха/ошибки и MAX haptics. Анимируй "
    "прежде всего transform/opacity; не строй UX на hover, не запускай бесконечный декор, "
    "не анимируй всё одновременно и обязательно уважай `prefers-reduced-motion`. Каждая "
    "анимация должна объяснять действие, изменение состояния или навигационный контекст.\n\n"
    "УСИЛЕНИЕ НАВЫКАМИ, НЕ ШАБЛОНАМИ. В system prompt есть короткий MAX "
    "CAPABILITY CATALOG. На первой полной сборке до первой записи продуктового кода "
    "обязательно по одному разу вызови read_skill(`ui-ux-pro-max`), "
    "read_skill(`product-flow`), read_skill(`art-direction`) и "
    "read_skill(`production-readiness`). Это расширяет "
    "творческий диапазон, но не выбирает пресет. После первого `see`, перед финальным "
    "`done`, ровно один раз вызови read_skill(`visual-evaluation`) и примени честную "
    "критику к уже отрисованному продукту. Дополнительно загрузи `interaction-motion`, "
    "ровно один подходящий domain-pack, `trust-safety` или `growth-analytics` только "
    "когда их trigger из каталога реально присутствует в брифе; не более трёх таких "
    "triggered packs за сборку. На точечной правке "
    "не трать ход на skill, если уже знаешь решение. Навык — это оптика, эвристики и "
    "сырьё для мышления: он не меняет бриф, не выбирает за тебя арт-дирекцию и не "
    "обязывает к конкретной компоновке. Не загружай всё подряд.\n\n"
    "ДОКАЗАТЕЛЬСТВО КАЧЕСТВА. Цикл: реализуй целиком → build до чистоты → "
    "runtime_check после последней записи → see через подписанную MAX-сессию. Если see "
    "возвращает broken/generic или конкретные проблемы, не объявляй done: примени "
    "точечную правку, снова build/runtime_check/see и повторяй до чистого visual verdict. "
    "Если QA-инфраструктура недоступна, не перезапускай её вслепую: сохрани зелёный "
    "продукт и опирайся на детерминированные проверки. Исправляй root-cause, не маскируй "
    "ошибку случайным переписыванием работающего приложения."
)


_EXPLORE_STALL_NUDGE = (
    "[LOOP GUARD] Several turns in a row without writing any file. Stop "
    "exploring — you have enough context. Your NEXT turn MUST call write_file "
    "or edit_file (or `build` and fix the errors it returns). Reading again "
    "makes no progress."
)
_DONE_WHEN_GREEN_NUDGE = (
    "[LOOP GUARD] The last build is CLEAN and you have written nothing for "
    "several turns. Do NOT keep re-reading. Finish the proof you still owe "
    "(runtime_check main routes, probe interactive actions, verify_isolation "
    "owned resources) and call done NOW."
)
_MAX_DONE_WHEN_GREEN_NUDGE = (
    "[LOOP GUARD] The MAX build is clean. Run runtime_check once after the final write, "
    "fix any concrete runtime error, then call done NOW. Do not call visual ceremony, "
    "generic probe or verify_isolation."
)

_MAX_NATIVE_VERIFICATION_OVERRIDE = (
    "MAX VERIFICATION OVERRIDE (takes precedence over the generic web-app rules above): "
    "MAX uses signed initData and an authenticated preview session. The generic probe and "
    "verify_isolation tools cannot prove this runtime and are not available in a MAX build. "
    "The see tool DOES receive a signed MAX preview session. Finish "
    "the complete source product, run build until clean, run runtime_check after the final "
    "write, then call see; the executor supplies a signed MAX preview session. A "
    "broken/generic verdict is not proof: apply the concrete fixes, rebuild, runtime_check "
    "and see again until the visual verdict is clean. If visual QA reports unavailable, do "
    "not retry it blindly."
)

_MAX_REFERENCE_PREAMBLE = (
    "Ты — автономный инженер, который за один непрерывный проход строит "
    "полноценный MAX Mini App. MAX runtime — только технический адаптер: он не задаёт "
    "layout, палитру, навигацию или стиль продукта. Коротко изучи нужные контракты, затем "
    "сразу создавай продукт по брифу. Не добавляй платформенную визуальную оболочку, "
    "постоянный юридический футер или отдельный дизайн MAX. "
    "Не подменяй функции текстом, TODO или декоративными кнопками. Не останавливайся "
    "на частичном результате и не объявляй успех словами. Большой интерфейс раскладывай "
    "по небольшим компонентам: суммарное содержимое write_file/edit_file за один ответ "
    "должно быть короче 24 000 символов, иначе аргументы инструмента будут обрезаны.\n\n"
    "Сохрани управляемую MAX-обвязку: подписанный initData, useMaxApp/профиль, "
    "integration-client, webhook и закрытые Studio-файлы. Пользовательские данные "
    "бери из MAX и управляемого серверного хранилища; не зашивай демо-профили, "
    "историю, метрики или секреты. Не создавай параллельную email-регистрацию. "
    "Но определения товаров, услуг, тарифов, категорий и вариантов, прямо запрошенные "
    "в брифе, — это содержимое продукта, а не фальшивые пользовательские данные. Если "
    "управляемый config.content пуст, добавь небольшой честный fallback-каталог по брифу "
    "с названиями и ценами, чтобы первый запуск показывал рабочий сценарий; при наличии "
    "config.content используй его. Никогда не подменяй этим реальные заказы или историю. "
    "src/app/globals.css — обычный глобальный CSS, не CSS Module: никогда не используй "
    "в нём `:global(...)`.\n\n"
    "Надёжный цикл: минимально прочитай нужные файлы → пиши полные продуктовые файлы "
    "→ build → исправь каждую реальную ошибку → runtime_check корневого экрана после "
    "последней записи. Если runtime_check красный, используй возвращённый файл и текст "
    "ошибки для минимальной точечной edit_file, затем снова build/runtime_check; не повторяй "
    "красную проверку без исправления. После чистых build и runtime_check вызови done: "
    "система сама проверит гидратацию и наличие реального продукта перед публикацией. "
    "Не трать ходы на визуальную церемонию, навыки, планирование или внешнее исследование, "
    "если конкретная библиотечная сигнатура не требует docs."
)

_MAX_REFERENCE_EDIT_PREAMBLE = (
    "Ты — автономный AI-агент для точечной правки существующего MAX Mini App. "
    "Сначала прочитай только целевой участок, затем внеси минимальное изменение, "
    "сохрани все остальные экраны, данные и сценарии. Не переписывай весь продукт, "
    "не меняй визуальное направление без прямого запроса и не трогай управляемое "
    "MAX-ядро. Не добавляй платформенную оболочку или юридический футер. После последней "
    "записи исправь фактические ошибки build/runtime_check и заверши на зелёной версии; "
    "система сама проверит гидратацию продукта перед публикацией. "
    "Не создавай демо-данные, секреты, параллельную "
    "email-авторизацию, API или прямой доступ к БД."
)


def native_system_prompt(
    stack_guide: str,
    skills: str | None = None,
    *,
    stable_max_loop: bool = False,
    stable_max_edit: bool = False,
    reference_max_loop: bool = False,
    reference_max_edit: bool = False,
) -> str:
    """Native-tools system prompt: a short tool-loop preamble + the stack guide (+
    skills). Deliberately DROPS the text-``<omnia:action>`` LOOP_PROTOCOL — the tool
    schemas ARE the protocol now, so keeping it would only confuse a native model."""
    guide = (stack_guide or "").strip()
    # The stable MAX path needs a short product-first preamble. The generic web
    # prompt advertises unavailable planning/probe tools and is large enough to
    # encourage repeated exploration of the already-known starter.
    _ = reference_max_loop, reference_max_edit
    parts = [
        (_MAX_REFERENCE_EDIT_PREAMBLE if stable_max_edit else _MAX_REFERENCE_PREAMBLE)
        if stable_max_loop
        else _NATIVE_PREAMBLE,
        guide,
    ]
    # Stable MAX deliberately omits read_skill and its catalog: advertising
    # unavailable design ceremony wastes paid turns and prompt budget.
    if skills and skills.strip() and not stable_max_loop:
        parts.append(skills.strip())
    return "\n\n".join(p for p in parts if p)


def _stable_max_entry_focus_task(task: str, written: Mapping[str, str]) -> str:
    """Compact a stale pre-entry transcript into one bounded composition turn."""

    support: list[str] = []
    budget = 16_000
    for path, content in written.items():
        if path == _STABLE_MAX_PRODUCT_ENTRY or budget <= 0:
            continue
        excerpt = content[: min(1_800, budget)]
        support.append(f"FILE {path}\n{excerpt}")
        budget -= len(excerpt)
    return (
        "[FOCUSED PRODUCT ENTRY]\n"
        "The supporting layer below is already written. Do not read or rewrite it. "
        f"Now write ONLY `{_STABLE_MAX_PRODUCT_ENTRY}` as a compact complete screen "
        "composition. Import useful modules when their exports are clear; otherwise keep "
        "the complete user-facing UI in ProductApp. Stay below 24000 output characters. "
        "Include all requested screens, navigation, loading/empty/error/success states, and "
        "real interactions; this is a full application, not a placeholder. Requested product "
        "definitions such as catalog items, services, plans, categories and prices are valid "
        "reference content: provide a compact brief-specific fallback when managed content is "
        "empty, but never invent user accounts, orders, history, metrics or success records.\n\n"
        f"ORIGINAL TASK\n{task}\n\n"
        "ALREADY WRITTEN SUPPORT\n" + "\n\n".join(support)
    )


def _stable_max_visual_repair_task(
    task: str,
    feedback: str,
    written: Mapping[str, str],
) -> str:
    """Replace a stale build transcript with one evidence-led visual repair turn."""

    preferred = [_STABLE_MAX_PRODUCT_ENTRY, "src/app/globals.css"]
    paths = [*preferred, *(path for path in written if path not in preferred)]
    sources: list[str] = []
    remaining = 72_000
    for path in paths:
        content = written.get(path, "")
        if not content or remaining <= 0:
            continue
        excerpt = content[:remaining]
        remaining -= len(excerpt)
        sources.append(f"CURRENT `{path}`\n```\n{excerpt}\n```")
    return (
        "[FOCUSED VISUAL RESCUE]\n"
        "The application already compiles and runs, but the rendered desktop/mobile result "
        "is below the production visual floor. The current source and exact visual verdict "
        "are included below, so do not read, list, grep, plan, or explain. Your next action "
        "must write_file or edit_file and apply every concrete issue in one coherent pass. "
        "When the verdict specifically requires real photography or illustration, you may "
        "instead call generate_media(kind='image') once, then embed its returned hosted URL "
        "with edit_file on the following turn. Never substitute an icon or gradient placeholder. "
        "When both component markup and stylesheet need changes, emit both edits in this "
        "turn when they fit; otherwise finish the component first and the executor will "
        "offer one bounded stylesheet-or-build turn next. "
        "Preserve working behavior, MAX integration, honest empty states, and accessibility. "
        "Never fix a hidden CTA by floating it over scrollable choices: compact or stage the "
        "choices and keep the action in flow, or reserve an opaque dock plus an actual spacer. "
        "A preview identity can be absent; render neutral copy and never expose synthetic "
        "Пользователь/User/Guest names. "
        "Catalog, menu, service or plan definitions explicitly requested in the brief are "
        "product reference content, not fake user records. If managed content is empty, render "
        "a compact brief-specific fallback with real labels and prices instead of leaving the "
        "requested primary screen empty. "
        "Prefer exact edits; keep total tool content below 24000 characters. Do not add fake "
        "user history, completed activity, statistics, testimonials, or decorative filler.\n\n"
        f"ORIGINAL TASK\n{task[:12_000]}\n\n"
        f"LATEST RENDERED VERDICT\n{feedback[:8_000]}\n\n"
        "CURRENT PRODUCT SOURCE\n" + "\n\n".join(sources)
    )


def _stable_max_source_repair_task(
    task: str,
    gap: str,
    written: Mapping[str, str],
) -> str:
    """Compact a green build around one objective source-contract gap."""

    preferred = [
        _STABLE_MAX_PRODUCT_ENTRY,
        "src/app/globals.css",
        ".omnia/max-design-spec.json",
    ]
    paths = [*preferred, *(path for path in written if path not in preferred)]
    sources: list[str] = []
    remaining = 72_000
    for path in paths:
        content = written.get(path, "")
        if not content or remaining <= 0:
            continue
        excerpt = content[:remaining]
        remaining -= len(excerpt)
        sources.append(f"CURRENT `{path}`\n```\n{excerpt}\n```")
    return (
        "[FOCUSED SOURCE CONTRACT REPAIR]\n"
        "The application already compiles. One objective production-contract gap remains "
        "and the current source is included below. Do not read, list, grep, browse, plan, "
        "or explain. Your next action must write_file or edit_file and fix exactly this gap "
        "without redesigning working screens. Use the managed MAX integration primitives "
        "named in the gap; keep provider-connected and unavailable states honest. Preserve "
        "existing behavior, visual quality, accessibility, and real persisted actions. "
        "Keep total tool content below 24000 characters. After the edit, build will be "
        "required automatically.\n\n"
        f"ORIGINAL TASK\n{task[:12_000]}\n\n"
        f"EXACT CONTRACT GAP\n{gap[:8_000]}\n\n"
        "CURRENT PRODUCT SOURCE\n" + "\n\n".join(sources)
    )


def _is_stable_max_source_gap(gap: str | None) -> bool:
    """Distinguish editable contract debt from skill/runtime/visual proof debt."""

    if not gap:
        return False
    lowered = gap.casefold()
    return not (
        "src/app/globals.css" in gap
        or "proof" in lowered
        or lowered.startswith("read required max capability packs")
        or lowered.startswith("run runtime_check")
        or lowered.startswith("run see")
        or lowered.startswith("read visual-evaluation")
    )


async def _call_messages(
    client: httpx.AsyncClient,
    url: str,
    convo: list[dict[str, Any]],
    system: str,
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    run_id: str | None = None,
    message_id: str | None = None,
    free: bool = False,
    stage: str = "native_agent",
    tools: list[dict[str, Any]] | None = None,
    model: str = _MODEL,
    thinking_budget: int = _THINKING_BUDGET,
    turn_id: str | None = None,
    resume_count: int = 0,
) -> dict[str, Any]:
    """One native /v1/messages call with 429 (concurrency) retry. Returns the parsed
    Anthropic response dict, or raises the last error."""
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "thinking": {"type": "enabled", "budget_tokens": thinking_budget},
        # Prompt caching: cache the stable system prompt + tool schemas, and a
        # moving breakpoint on the transcript tail (see _with_incremental_cache).
        "system": _system_blocks(system),
        "tools": tools if tools is not None else _TOOLS_CACHED,
        "tool_choice": {"type": "auto"},
        "messages": _with_incremental_cache(convo),
    }
    if user_id:
        payload["user"] = user_id
    last: Exception | None = None
    for attempt in range(_CALL_RETRIES):
        # Every provider attempt is attributable. The gateway persists these
        # fields with the provider usage row, including a successful retry.
        payload["metadata"] = {
            "user_id": user_id,
            "project_id": project_id,
            "run_id": run_id,
            "message_id": message_id,
            "free": free,
            "stage": stage,
            "retry_count": attempt,
            "turn_id": turn_id,
            "resume_count": max(0, int(resume_count)),
        }
        try:
            r = await client.post(url, json=payload, timeout=_gateway_timeout())
            try:
                parsed_body = r.json()
            except ValueError as exc:
                if 200 <= r.status_code < 300:
                    raise AmbiguousPaidCallError(r.status_code) from exc
                parsed_body = None
            if 200 <= r.status_code < 300 and not isinstance(parsed_body, dict):
                raise AmbiguousPaidCallError(r.status_code)
            try:
                error_payload = parsed_body.get("error") or {}
                error_type = str(error_payload.get("code") or error_payload.get("type") or "")
            except AttributeError:
                error_type = ""
            if error_type == "provider_response_timeout":
                raise ProviderResponseTimeoutError(r.status_code)
            if error_type == "paid_call_ambiguous":
                raise AmbiguousPaidCallError(r.status_code)
            trusted_rate_limit = error_type in {
                "rate_limit",
                "rate_limited",
                "concurrency_limited",
            }
            if r.status_code == 429 and trusted_rate_limit:
                await asyncio.sleep(6.0 * (attempt + 1))
                last = RuntimeError(f"429 concurrency (attempt {attempt + 1})")
                continue
            if r.status_code in {408, 425, 429} or r.status_code >= 500:
                raise AmbiguousPaidCallError(r.status_code)
            if r.status_code == 409:
                if error_type == "run_budget_exhausted":
                    raise SpendBudgetExceeded
            # Retrying a rejected request cannot repair credentials, balance,
            # endpoint or payload validation. Surface only the numeric status:
            # response bodies may contain provider diagnostics or secrets.
            if 400 <= r.status_code < 500 and r.status_code not in {408, 425, 429}:
                raise PermanentProviderError(r.status_code)
            r.raise_for_status()
            assert isinstance(parsed_body, dict)
            content = parsed_body.get("content")
            if not isinstance(content, list) or not content:
                raise AmbiguousPaidCallError(r.status_code)
            recognised = False
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type in {"text", "thinking", "redacted_thinking"}:
                    recognised = True
                elif (
                    block_type == "tool_use"
                    and isinstance(block.get("id"), str)
                    and isinstance(block.get("name"), str)
                    and isinstance(block.get("input"), dict)
                ):
                    recognised = True
            if not recognised:
                raise AmbiguousPaidCallError(r.status_code)
            return parsed_body
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # These failures happen before a response-bearing connection is
            # available, so the outer MAX reconnect cycle may safely retry.
            last = exc
            await asyncio.sleep(min(45.0, 4.0 * (2**attempt)))
        except httpx.HTTPError as exc:
            # Once request transmission may have started, a timeout/protocol
            # loss can hide a provider response that was already billed.  Do
            # not let the outer loop repeat that logical paid turn.
            raise AmbiguousPaidCallError(None) from exc
    raise last or RuntimeError("messages call failed")


class PermanentProviderError(RuntimeError):
    """A provider rejection that reconnecting with the same request cannot fix."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"provider rejected request (HTTP {status_code})")


class AmbiguousPaidCallError(RuntimeError):
    """A provider may have billed this call; repeating it would risk double spend."""

    def __init__(self, status_code: int | None) -> None:
        self.status_code = status_code
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"paid provider call has ambiguous settlement{suffix}")


class ProviderResponseTimeoutError(RuntimeError):
    """Gateway closed a provider response whose body stopped making progress."""

    def __init__(self, status_code: int | None) -> None:
        self.status_code = status_code
        super().__init__("provider response body timed out")


class SpendBudgetExceeded(RuntimeError):
    """The gateway stopped this run before another paid provider request."""


async def run_native_build(
    *,
    system: str,
    task: str,
    execute: Callable[[Action], Awaitable[dict[str, Any]]],
    user_id: Any = None,
    project_id: Any = None,
    run_id: Any = None,
    message_id: Any = None,
    free: bool = False,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    completion_check: Callable[[Mapping[str, str], Mapping[str, int]], str | None] | None = None,
    enforce_max_skill_lifecycle: bool = False,
    reference_max_loop: bool = False,
    max_steps: int | None = 24,
    model: str = _MODEL,
    stable_max_loop: bool = False,
) -> AgentResult:
    """Drive one native tool-use loop until the model calls ``done`` after a clean
    build or reaches ``max_steps``. MAX uses the same bounded loop as the proven
    pre-Gemini production path; the gateway's durable monetary/request fuse is an
    independent final guard, not the loop controller.

    ``system`` is the stack/system prompt (reuse ``agent_builder.build_system_prompt``);
    ``task`` is the user's request. One model, full transcript (thinking preserved),
    fact-gate = the ``build`` tool. No lossy window — instead the full prefix
    (system + tools + transcript) rides Anthropic prompt caching every turn, so
    resending it is ~90% cheaper than a fresh write (see _call_messages).
    """
    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/messages"

    convo: list[dict[str, Any]] = [{"role": "user", "content": task}]
    written: dict[str, str] = {}
    last_build_ok: bool | None = None
    last_build_error_paths: frozenset[str] = frozenset()
    last_build_error_text = ""
    repair_reads_since_build: set[str] = set()
    repair_reread_paths: set[str] = set()
    repair_source_cache: dict[str, str] = {}
    repair_context_compacted = False
    wrote_since_build = False
    no_write_turns = 0  # consecutive assistant turns with no successful write
    noop_write_turns = 0  # consecutive turns whose attempted writes changed zero bytes
    infra_dead_turns = 0  # consecutive turns where EVERY tool op died on infra
    successful_tools: dict[str, int] = {}
    successful_skill_ids: set[str] = set()
    proof_after_write: set[str] = set()
    visual_feedback_step: int | None = None
    visual_feedback_detail = ""
    visual_context_compacted_step: int | None = None
    visual_media_generated_step: int | None = None
    visual_repair_attempts = 0
    visual_repair_paths: set[str] = set()
    visual_finish_pending = False
    source_repair_context_gap: str | None = None
    last_green_see_step: int | None = None
    pending_visual_evaluation_step: int | None = None
    visual_evaluation_ready = False
    provider_reconnect_cycles = 0
    provider_timeout_resumes = 0
    provider_turn_index = 0
    truncated_no_write_turns = 0
    turns_without_product_entry = 0
    entry_focus_compacted = False
    prewrite_inspection_paths: set[str] = set()
    prewrite_inspection_ops = 0
    prewrite_inspection_exhausted = False

    # Stable MAX builds get a generous but finite turn and wall-clock envelope.
    # Both limits are independent so a slow provider or a model that keeps
    # finding work must still terminate without publishing partial files.
    max_runtime = "MAX VERIFICATION OVERRIDE" in system or reference_max_loop or stable_max_loop
    max_lifecycle = max_runtime and completion_check is not None and enforce_max_skill_lifecycle
    effective_max_steps = (
        max(1, int(settings.agent_builder_max_runtime_steps))
        if max_runtime
        else max(1, int(40 if max_steps is None else max_steps))
    )
    runtime_deadline = (
        asyncio.get_running_loop().time() + max(1, int(settings.agent_builder_max_runtime_seconds))
        if max_runtime
        else None
    )

    def _evidence() -> dict[str, int]:
        result = dict(successful_tools)
        for skill in successful_skill_ids:
            result[f"skill:{skill}"] = 1
        if visual_evaluation_ready:
            result["visual_evaluation_after_see"] = 1
        for tool in proof_after_write:
            result[f"{tool}_after_write"] = 1
        return result

    def _completion_gap() -> str | None:
        if completion_check is None:
            return None
        try:
            return completion_check(written, _evidence())
        except Exception as exc:
            log.exception("agent_native.completion_check_failed")
            return f"Product acceptance check failed: {type(exc).__name__}."

    async def _finish_without_provider(*, steps: int, reason: str, detail: str) -> AgentResult:
        """Stop provider traffic and prove the tree with one local build only."""
        try:
            final_build = await execute(Action(name="build", args={}, raw=""))
        except Exception as exc:
            final_build = {"ok": False, "error": f"final build probe crashed: {exc}"}
        if emit:
            await emit(
                "agent.step",
                {
                    "step": steps,
                    "action": "build",
                    "path": "",
                    "detail": _step_detail("build", Action("build", {}, ""), final_build),
                    "ok": bool(final_build.get("ok")),
                },
            )
        if final_build.get("ok"):
            successful_tools["build"] = successful_tools.get("build", 0) + 1
            proof_after_write.add("build")
            gap = _completion_gap()
            # A provider turn limit must not turn forgotten verification clicks
            # into a user-visible failed build. When the source is already green
            # and the remaining product-contract gap asks only for deterministic
            # proof, run those read-only checks locally. Functional/source gaps
            # still return to the caller's autonomous repair segment.
            local_proofs = 0
            while gap and local_proofs < 4:
                action: Action | None = None
                if "runtime_check" in gap:
                    evidence = _evidence()
                    if evidence.get("runtime_check_after_write", 0) < 1:
                        action = Action("runtime_check", {"path": "/"}, "")
                if action is None and "probe" in gap:
                    action = Action(
                        "probe",
                        {"method": "GET", "path": "/api/omnia/actions"},
                        "",
                    )
                if action is None and "see" in gap:
                    action = Action("see", {"path": "/"}, "")
                if action is None:
                    break
                try:
                    proof = await execute(action)
                except Exception as exc:
                    proof = {"ok": False, "error": f"local proof crashed: {exc}"}
                local_proofs += 1
                if emit:
                    await emit(
                        "agent.step",
                        {
                            "step": steps,
                            "action": action.name,
                            "path": action.path,
                            "detail": _step_detail(action.name, action, proof),
                            "ok": bool(proof.get("ok")),
                        },
                    )
                if not proof.get("ok"):
                    break
                if action.name == "see" and (
                    proof.get("proof_unavailable") or proof.get("skipped")
                ):
                    # Fail-soft visual infrastructure is not production proof.
                    # Preserve the contract gap and return an honest red result.
                    break
                successful_tools[action.name] = successful_tools.get(action.name, 0) + 1
                proof_after_write.add(action.name)
                gap = _completion_gap()
            if gap:
                return AgentResult(
                    done=False,
                    summary=gap,
                    files=written,
                    steps=steps,
                    transcript=convo,
                    stop_reason="max_steps" if reason == "max_steps" else f"{reason}_red",
                )
            if (
                max_runtime
                and completion_check is None
                and reason in {"spend_budget", "provider_stopped", "provider_rejected"}
            ):
                # A clean compile proves only that the old tree still builds. A
                # forced stop during a MAX edit has no product acceptance check,
                # so reporting green could falsely claim an untouched request is
                # complete. Let the caller restore the previous snapshot instead.
                return AgentResult(
                    done=False,
                    summary=detail,
                    files=written,
                    steps=steps,
                    transcript=convo,
                    stop_reason=f"{reason}_red",
                )
            return AgentResult(
                done=True,
                summary=(
                    "Готово — генерация остановлена без дополнительных запросов к провайдеру; "
                    "текущая версия приложения проверена и работает."
                ),
                files=written,
                steps=steps,
                transcript=convo,
                stop_reason=f"{reason}_green",
            )
        return AgentResult(
            done=False,
            summary=str(final_build.get("detail") or final_build.get("error") or detail),
            files=written,
            steps=steps,
            transcript=convo,
            stop_reason=f"{reason}_red",
        )

    async with httpx.AsyncClient() as client:
        for step in range(effective_max_steps):
            if (
                runtime_deadline is not None
                and asyncio.get_running_loop().time() >= runtime_deadline
            ):
                return await _finish_without_provider(
                    steps=step,
                    reason="generation_deadline",
                    detail="generation wall-clock deadline exceeded",
                )
            if pending_visual_evaluation_step is not None and step > pending_visual_evaluation_step:
                visual_evaluation_ready = True
            if (
                stable_max_loop
                and visual_feedback_step is not None
                and visual_context_compacted_step != visual_feedback_step
            ):
                convo = [
                    {
                        "role": "user",
                        "content": _stable_max_visual_repair_task(
                            task,
                            visual_feedback_detail,
                            written,
                        ),
                    }
                ]
                visual_context_compacted_step = visual_feedback_step
            if (
                stable_max_loop
                and not entry_focus_compacted
                and _STABLE_MAX_PRODUCT_ENTRY not in written
                and (
                    len(written) >= _STABLE_MAX_SUPPORT_FILE_LIMIT
                    or turns_without_product_entry >= _STABLE_MAX_ENTRY_FOCUS_AT
                )
            ):
                convo = [
                    {
                        "role": "user",
                        "content": _stable_max_entry_focus_task(task, written),
                    }
                ]
                entry_focus_compacted = True
            force_entry_write = (
                stable_max_loop
                and _STABLE_MAX_PRODUCT_ENTRY not in written
                and (
                    turns_without_product_entry >= _STABLE_MAX_FIRST_WRITE_AT
                    or entry_focus_compacted
                    or prewrite_inspection_exhausted
                )
            )
            repair_mode = stable_max_loop and last_build_ok is False
            force_repair_write = repair_mode and not wrote_since_build
            force_repair_verify = repair_mode and wrote_since_build
            force_visual_finish = stable_max_loop and visual_finish_pending and not repair_mode
            # A successful product write is not permission to redesign the same
            # screen again. Live MAX runs repeatedly rewrote ProductApp without
            # ever compiling it because every rewrite reset the no-write guard.
            # Force a deterministic build immediately after the initial product
            # composition and after every green/visual revision. Red-build
            # repairs keep their existing edit-then-verify flow below.
            force_build_after_write = (
                stable_max_loop
                and _STABLE_MAX_PRODUCT_ENTRY in written
                and wrote_since_build
                and not repair_mode
                and not force_visual_finish
            )
            if (
                force_repair_write
                and last_build_error_paths
                and not repair_context_compacted
                and all(
                    path in written or path in repair_source_cache
                    for path in last_build_error_paths
                )
            ):
                convo = [
                    {
                        "role": "user",
                        "content": _stable_max_compact_repair_task(
                            last_build_error_text,
                            last_build_error_paths,
                            {**repair_source_cache, **written},
                        ),
                    }
                ]
                repair_context_compacted = True
            force_repair_edit_only = force_repair_write and repair_context_compacted
            force_progress = (
                stable_max_loop
                and no_write_turns >= _STABLE_MAX_FIRST_WRITE_AT
                and _STABLE_MAX_PRODUCT_ENTRY in written
                and (last_build_ok is None or wrote_since_build)
            )
            completion_gap = _completion_gap()
            force_style_write = (
                stable_max_loop
                and last_build_ok is True
                and not wrote_since_build
                and completion_gap is not None
                and "src/app/globals.css" in completion_gap
            )
            force_source_repair = (
                stable_max_loop
                and last_build_ok is True
                and not wrote_since_build
                and visual_feedback_step is None
                and _is_stable_max_source_gap(completion_gap)
            )
            if force_source_repair and source_repair_context_gap != completion_gap:
                convo = [
                    {
                        "role": "user",
                        "content": _stable_max_source_repair_task(
                            task,
                            str(completion_gap),
                            written,
                        ),
                    }
                ]
                source_repair_context_gap = completion_gap
            force_proof = (
                stable_max_loop
                and last_build_ok is True
                and not wrote_since_build
                and visual_feedback_step is None
                and completion_gap is not None
                and "runtime_check" in completion_gap
            )
            proof_gap = completion_gap.casefold() if completion_gap is not None else ""
            force_runtime_proof = force_proof and "runtime_check" in proof_gap
            force_visual_repair = (
                stable_max_loop
                and visual_feedback_step is not None
                and step >= visual_feedback_step + 1
            )
            force_product_progress = (
                force_entry_write
                or force_visual_finish
                or force_build_after_write
                or force_style_write
                or force_source_repair
                or force_repair_write
                or force_repair_verify
                or force_progress
            )
            call_stage = (
                "build_plan"
                if step == 0
                else "verification"
                if last_build_ok is True and not wrote_since_build
                else "native_agent"
            )
            try:
                remaining_seconds = (
                    max(0.1, runtime_deadline - asyncio.get_running_loop().time())
                    if runtime_deadline is not None
                    else None
                )
                async with asyncio.timeout(remaining_seconds):
                    resp = await _call_messages(
                        client,
                        url,
                        convo,
                        system,
                        user_id=str(user_id) if user_id else None,
                        project_id=str(project_id) if project_id else None,
                        run_id=str(run_id) if run_id else None,
                        message_id=str(message_id) if message_id else None,
                        free=free,
                        stage=call_stage,
                        turn_id=f"{run_id or 'run'}:{provider_turn_index}",
                        resume_count=provider_timeout_resumes,
                        tools=(
                            _STABLE_MAX_ENTRY_ONLY_TOOLS_CACHED
                            if entry_focus_compacted and _STABLE_MAX_PRODUCT_ENTRY not in written
                            else _STABLE_MAX_VISUAL_FINISH_TOOLS_CACHED
                            if force_visual_finish
                            else _STABLE_MAX_BUILD_ONLY_TOOLS_CACHED
                            if force_build_after_write
                            else _STABLE_MAX_STYLE_ONLY_TOOLS_CACHED
                            if force_style_write
                            else _STABLE_MAX_FIRST_WRITE_TOOLS_CACHED
                            if force_source_repair
                            else _STABLE_MAX_RUNTIME_ONLY_TOOLS_CACHED
                            if force_runtime_proof
                            else _STABLE_MAX_PROOF_TOOLS_CACHED
                            if force_proof
                            else _STABLE_MAX_VISUAL_REPAIR_TOOLS_CACHED
                            if force_visual_repair
                            else _STABLE_MAX_REPAIR_VERIFY_TOOLS_CACHED
                            if force_repair_verify
                            else _STABLE_MAX_REPAIR_TOOLS_CACHED
                            if force_repair_edit_only and repair_reread_paths
                            else _STABLE_MAX_REPAIR_EDIT_ONLY_TOOLS_CACHED
                            if force_repair_edit_only
                            else _STABLE_MAX_PROGRESS_TOOLS_CACHED
                            if force_progress
                            else _STABLE_MAX_REPAIR_TOOLS_CACHED
                            if force_repair_write
                            else _STABLE_MAX_FIRST_WRITE_TOOLS_CACHED
                            if force_entry_write
                            else _STABLE_MAX_TOOLS_CACHED
                            if stable_max_loop
                            else _MAX_REFERENCE_TOOLS_CACHED
                            if reference_max_loop
                            else _MAX_TOOLS_CACHED
                            if max_runtime
                            else _TOOLS_CACHED
                        ),
                        model=model,
                        thinking_budget=(
                            _ENTRY_FOCUS_THINKING_BUDGET
                            if entry_focus_compacted and _STABLE_MAX_PRODUCT_ENTRY not in written
                            else _THINKING_BUDGET
                        ),
                    )
                provider_turn_index += 1
                provider_timeout_resumes = 0
            except TimeoutError:
                log.warning("agent_native.generation_deadline", step=step)
                return await _finish_without_provider(
                    steps=step,
                    reason="generation_deadline",
                    detail="generation wall-clock deadline exceeded during provider call",
                )
            except SpendBudgetExceeded:
                log.warning("agent_native.spend_budget_exhausted", step=step)
                if emit:
                    await emit(
                        "agent.step",
                        {
                            "step": step,
                            "action": "spend_budget",
                            "path": "",
                            "detail": (
                                "Достигнут безопасный лимит расходов этой генерации. "
                                "Новые запросы к LLM остановлены; проверяю уже "
                                "собранную версию локально без дополнительных списаний."
                            ),
                            "ok": False,
                        },
                    )
                return await _finish_without_provider(
                    steps=step,
                    reason="spend_budget",
                    detail="safe generation spend limit reached",
                )
            except ProviderResponseTimeoutError as exc:
                provider_timeout_resumes += 1
                log.warning(
                    "agent_native.provider_response_resuming",
                    step=step,
                    status_code=exc.status_code,
                    resume_count=provider_timeout_resumes,
                )
                if max_runtime and provider_timeout_resumes <= _MAX_PROVIDER_TIMEOUT_RESUMES:
                    if emit:
                        await emit(
                            "agent.step",
                            {
                                "step": step,
                                "action": "provider_resume",
                                "path": "",
                                "detail": (
                                    "Длинный ответ перестал поступать. Продолжаю тот же "
                                    "логический шаг с устойчивым идентификатором; уже "
                                    "завершённый ответ будет получен из журнала без повтора."
                                ),
                                "ok": False,
                            },
                        )
                    await asyncio.sleep(min(15.0, 3.0 * provider_timeout_resumes))
                    continue
                return await _finish_without_provider(
                    steps=step,
                    reason="provider_response_timeout",
                    detail="provider response timeout recovery exhausted",
                )
            except AmbiguousPaidCallError as exc:
                log.warning(
                    "agent_native.max_paid_call_ambiguous",
                    step=step,
                    status_code=exc.status_code,
                )
                if emit:
                    await emit(
                        "agent.step",
                        {
                            "step": step,
                            "action": "accounting_guard",
                            "path": "",
                            "detail": (
                                "Ответ платного вызова неоднозначен. Повтор отключён, "
                                "чтобы исключить двойное списание; проверяю уже "
                                "собранную версию локально."
                            ),
                            "ok": False,
                        },
                    )
                return await _finish_without_provider(
                    steps=step,
                    reason="provider_stopped",
                    detail="paid provider call was not retried after ambiguous settlement",
                )
            except PermanentProviderError as exc:
                log.warning(
                    "agent_native.max_provider_rejected",
                    step=step,
                    status_code=exc.status_code,
                )
                if emit:
                    await emit(
                        "agent.step",
                        {
                            "step": step,
                            "action": "provider_rejected",
                            "path": "",
                            "detail": (
                                f"AI-провайдер отклонил запрос (HTTP {exc.status_code}); "
                                "повтор не поможет. "
                                "Останавливаю новые списания и проверяю уже собранную версию."
                            ),
                            "ok": False,
                        },
                    )
                return await _finish_without_provider(
                    steps=step,
                    reason="provider_rejected",
                    detail=f"provider rejected request (HTTP {exc.status_code})",
                )
            except Exception as exc:
                if max_runtime:
                    provider_reconnect_cycles += 1
                    log.warning(
                        "agent_native.max_provider_reconnecting",
                        step=step,
                        error=type(exc).__name__,
                        reconnect_cycle=provider_reconnect_cycles,
                    )
                    if provider_reconnect_cycles >= _MAX_PROVIDER_RECONNECT_CYCLES:
                        if emit:
                            await emit(
                                "agent.step",
                                {
                                    "step": step,
                                    "action": "provider_stopped",
                                    "path": "",
                                    "detail": (
                                        "LLM-провайдер трижды подряд не ответил. Новые "
                                        "платные запросы остановлены; проверяю уже "
                                        "собранную версию локально."
                                    ),
                                    "ok": False,
                                },
                            )
                        return await _finish_without_provider(
                            steps=step,
                            reason="provider_stopped",
                            detail=f"gateway error after reconnects: {type(exc).__name__}",
                        )
                    if emit:
                        await emit(
                            "agent.step",
                            {
                                "step": step,
                                "action": "provider_retry",
                                "path": "",
                                "detail": (
                                    "Связь с LLM временно прервалась; сборка "
                                    "не остановлена и продолжится автоматически."
                                ),
                                "ok": False,
                            },
                        )
                    await asyncio.sleep(15.0)
                    continue
                return await _finish_without_provider(
                    steps=step,
                    reason="provider_stopped",
                    detail=f"gateway error: {exc}",
                )

            content = resp.get("content")
            if not isinstance(content, list):
                if max_runtime:
                    provider_reconnect_cycles += 1
                    log.warning(
                        "agent_native.max_malformed_provider_retry",
                        step=step,
                        reconnect_cycle=provider_reconnect_cycles,
                    )
                    if provider_reconnect_cycles >= _MAX_PROVIDER_RECONNECT_CYCLES:
                        if emit:
                            await emit(
                                "agent.step",
                                {
                                    "step": step,
                                    "action": "provider_stopped",
                                    "path": "",
                                    "detail": (
                                        "LLM-провайдер трижды вернул повреждённый ответ. "
                                        "Новые запросы остановлены; проверяю уже "
                                        "собранную версию локально."
                                    ),
                                    "ok": False,
                                },
                            )
                        return await _finish_without_provider(
                            steps=step,
                            reason="provider_stopped",
                            detail="gateway returned malformed content repeatedly",
                        )
                    await asyncio.sleep(5.0)
                    continue
                return AgentResult(
                    done=False,
                    summary="malformed upstream (no content list)",
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="error",
                )
            provider_reconnect_cycles = 0
            # Echo the assistant turn VERBATIM — thinking blocks (with signatures)
            # MUST be preserved for the next turn or Anthropic rejects the round-trip.
            convo.append({"role": "assistant", "content": content})

            # Streaming (phase 8): surface Opus's own narration between tool calls to
            # the UI so the workspace reads «как переписка с Claude» — the model
            # explains what it's doing, live, next to the tool steps.
            if emit:
                _narration = _text_of(content)
                if _narration:
                    await emit("agent.text", {"step": step, "text": _narration})

            tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            if not tool_uses:
                _done = resp.get("stop_reason") == "end_turn"
                _text = _text_of(content)
                # Prose is not proof. Keep the turn inside the same lifecycle
                # until a real clean build exists; never ship a broken app because
                # the model happened to finish speaking.
                gap = _completion_gap() if _done and last_build_ok is True else None
                if _done and last_build_ok is True and not wrote_since_build and not gap:
                    return AgentResult(
                        done=True,
                        summary=_text or "Готово — приложение собрано и проверено.",
                        files=written,
                        steps=step + 1,
                        transcript=convo,
                        stop_reason="done_green",
                    )
                convo.append(
                    {
                        "role": "user",
                        "content": (
                            (gap + " " if gap else "")
                            + "Перед завершением обязательно вызови build. Если он красный — "
                            "почини ошибки; если чистый — устрани остаток acceptance contract "
                            "и вызови done."
                        ),
                    }
                )
                continue

            results: list[dict[str, Any]] = []
            done_summary: str | None = None
            wrote_this_turn = False
            noop_write_this_turn = False
            visual_proof_unavailable_this_turn = False
            visual_quality_exhausted_this_turn = False
            visual_finish_satisfied_this_turn = False
            ops_this_turn = 0  # executed (non-done) tool ops this turn
            infra_this_turn = 0  # of those, how many died on infra
            for tu in tool_uses:
                name = tu.get("name", "")
                tu_id = tu.get("id", "")
                if name == "done":
                    if force_product_progress:
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu_id,
                                "is_error": True,
                                "content": (
                                    _STABLE_MAX_VISUAL_FINISH_REQUIRED
                                    if force_visual_finish
                                    else _STABLE_MAX_BUILD_REQUIRED
                                    if force_build_after_write
                                    else _STABLE_MAX_STYLE_REQUIRED
                                    if force_style_write
                                    else _STABLE_MAX_SOURCE_REPAIR_REQUIRED
                                    if force_source_repair
                                    else _STABLE_MAX_PROGRESS_REQUIRED
                                    if force_progress
                                    else _STABLE_MAX_REPAIR_VERIFY_REQUIRED
                                    if force_repair_verify
                                    else _STABLE_MAX_ENTRY_REQUIRED
                                    if force_entry_write
                                    else _stable_max_repair_required(last_build_error_paths)
                                    if force_repair_write
                                    else _STABLE_MAX_WRITE_REQUIRED
                                ),
                            }
                        )
                        continue
                    # Fact-gate: refuse a premature done if the model wrote files but
                    # never confirmed a CLEAN build afterwards.
                    premature = wrote_since_build or last_build_ok is not True
                    if premature:
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu_id,
                                "is_error": True,
                                "content": "Not done yet: run the `build` tool and make it "
                                "CLEAN (fix any errors) before calling done.",
                            }
                        )
                        continue
                    gap = _completion_gap()
                    if gap:
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu_id,
                                "is_error": True,
                                "content": "Not done yet: " + gap,
                            }
                        )
                        continue
                    done_summary = str((tu.get("input") or {}).get("summary", ""))
                    results.append({"type": "tool_result", "tool_use_id": tu_id, "content": "done"})
                    continue

                action = _tool_use_to_action(tu)
                if stable_max_loop:
                    action = _normalize_stable_max_action_path(action)
                lifecycle_error = ""
                if max_runtime and name == "read_skill":
                    skill_id = str(action.args.get("skill") or "").strip().casefold()
                    if skill_id in successful_skill_ids:
                        lifecycle_error = (
                            f"Capability pack `{skill_id}` is already loaded. Apply it instead "
                            "of spending another tool call."
                        )
                    elif (
                        max_lifecycle
                        and skill_id == MAX_REQUIRED_POST_SEE_SKILL
                        and (
                            "see" not in proof_after_write
                            or last_green_see_step is None
                            or step <= last_green_see_step
                        )
                    ):
                        lifecycle_error = (
                            "Run a green `see` after the latest product write, then load "
                            "`visual-evaluation` in the next model turn so it can inspect "
                            "the actual screenshot result."
                        )
                    elif max_lifecycle and skill_id not in {
                        *MAX_REQUIRED_PREWRITE_SKILLS,
                        MAX_REQUIRED_POST_SEE_SKILL,
                    }:
                        optional_skills = successful_skill_ids.difference(
                            {*MAX_REQUIRED_PREWRITE_SKILLS, MAX_REQUIRED_POST_SEE_SKILL}
                        )
                        if len(optional_skills) >= 3:
                            lifecycle_error = (
                                "The three optional capability-pack slots are already used. "
                                "Apply the loaded specialist guidance and continue building."
                            )
                elif max_lifecycle and name in {"write_file", "edit_file"}:
                    missing_skills: list[str] = []
                    if pending_visual_evaluation_step == step:
                        lifecycle_error = (
                            "Apply visual-evaluation in the next model turn after receiving "
                            "the capability result; a same-turn write cannot use it."
                        )
                    else:
                        missing_skills = [
                            skill
                            for skill in MAX_REQUIRED_PREWRITE_SKILLS
                            if skill not in successful_skill_ids
                        ]
                    if not lifecycle_error and missing_skills:
                        lifecycle_error = (
                            "Before the first product write, load the required capability packs: "
                            + ", ".join(missing_skills)
                            + "."
                        )

                obs: dict[str, Any]
                tool_executed = False
                allowed_progress_tools = (
                    {"build"}
                    if force_build_after_write
                    else {"edit_file", "build"}
                    if force_visual_finish
                    else {"write_file"}
                    if force_style_write
                    else {"write_file", "edit_file"}
                    if force_source_repair
                    else {"read_file", "edit_file", "build"}
                    if force_repair_verify
                    else {"read_file", "edit_file"}
                    if force_repair_edit_only and repair_reread_paths
                    else {"edit_file"}
                    if force_repair_edit_only
                    else {"read_file", "edit_file"}
                    if force_repair_write
                    else {"write_file", "edit_file", "build"}
                    if force_progress
                    else {"write_file", "edit_file"}
                )
                if _contains_history_placeholder(action):
                    # The gateway replaces large historical tool arguments with
                    # explicit markers. A long-running model can occasionally
                    # echo that marker back as if it were the file body. Never
                    # let transcript compaction destroy the live source; allow
                    # one fresh read of the failing path on the next turn.
                    repair_reads_since_build.discard(action.path)
                    repair_source_cache.pop(action.path, None)
                    if visual_feedback_step is not None:
                        # The next paid turn must receive a fresh, authoritative
                        # focused source bundle. Otherwise the compacted marker
                        # remains in the transcript and can be echoed repeatedly.
                        visual_context_compacted_step = None
                    obs = {"ok": False, "error": _HISTORY_PLACEHOLDER_WRITE_REJECTED}
                elif visual_proof_unavailable_this_turn and name == "see":
                    # Tool calls in one assistant response are planned before
                    # their results return. Execute at most one unavailable
                    # visual proof in the batch; the bounded retry already ran
                    # inside ``see_page``.
                    obs = {
                        "ok": False,
                        "error": "Visual QA is unavailable; repeated see was skipped.",
                    }
                elif force_runtime_proof and name != "runtime_check":
                    obs = {"ok": False, "error": _STABLE_MAX_RUNTIME_PROOF_REQUIRED}
                elif force_visual_finish and (
                    name not in {"edit_file", "build"}
                    or (name == "edit_file" and action.path != "src/app/globals.css")
                ):
                    obs = {"ok": False, "error": _STABLE_MAX_VISUAL_FINISH_REQUIRED}
                elif force_proof and name not in {"runtime_check", "see"}:
                    # Cached provider turns may still reference schemas from an
                    # earlier unrestricted phase. Enforce the proof transition at
                    # execution too, otherwise repeated read/grep calls can spend
                    # the entire generation budget after a green build.
                    obs = {"ok": False, "error": _STABLE_MAX_PROOF_REQUIRED}
                elif (
                    force_visual_repair
                    and name == "generate_media"
                    and visual_media_generated_step == visual_feedback_step
                ):
                    obs = {
                        "ok": False,
                        "error": (
                            "One visual asset is already generated for this rendered verdict. "
                            "Embed its returned URL with edit_file now."
                        ),
                    }
                elif (
                    force_visual_repair
                    and name
                    not in {
                        "write_file",
                        "edit_file",
                        "generate_media",
                    }
                    and not (force_build_after_write and name == "build")
                ):
                    # After one turn to inspect the concrete visual verdict,
                    # further search only inflates paid context and can hit the
                    # provider rate limit before the known repair is applied.
                    obs = {"ok": False, "error": _STABLE_MAX_VISUAL_REPAIR_REQUIRED}
                elif (
                    stable_max_loop
                    and _STABLE_MAX_PRODUCT_ENTRY not in written
                    and name in {"read_file", "list_dir", "grep", "docs"}
                    and (
                        prewrite_inspection_ops >= _STABLE_MAX_PREWRITE_INSPECTION_LIMIT
                        or (name == "read_file" and action.path in prewrite_inspection_paths)
                    )
                ):
                    if prewrite_inspection_ops >= _STABLE_MAX_PREWRITE_INSPECTION_LIMIT:
                        prewrite_inspection_exhausted = True
                    obs = {"ok": False, "error": _STABLE_MAX_INSPECTION_COMPLETE}
                elif (
                    entry_focus_compacted
                    and _STABLE_MAX_PRODUCT_ENTRY not in written
                    and (name != "write_file" or action.path != _STABLE_MAX_PRODUCT_ENTRY)
                ):
                    obs = {"ok": False, "error": _STABLE_MAX_ENTRY_NOW_REQUIRED}
                elif force_product_progress and name not in allowed_progress_tools:
                    # Some provider-compatible gateways keep earlier tool schemas
                    # available for the cached conversation even when this turn
                    # advertises only write/edit. Enforce the transition at the
                    # executor boundary too, so an old read tool cannot consume
                    # more paid turns after the bounded exploration window.
                    obs = {
                        "ok": False,
                        "error": (
                            _STABLE_MAX_VISUAL_FINISH_REQUIRED
                            if force_visual_finish
                            else _STABLE_MAX_BUILD_REQUIRED
                            if force_build_after_write
                            else _STABLE_MAX_STYLE_REQUIRED
                            if force_style_write
                            else _STABLE_MAX_SOURCE_REPAIR_REQUIRED
                            if force_source_repair
                            else _STABLE_MAX_PROGRESS_REQUIRED
                            if force_progress
                            else _STABLE_MAX_REPAIR_VERIFY_REQUIRED
                            if force_repair_verify
                            else _STABLE_MAX_ENTRY_REQUIRED
                            if force_entry_write
                            else _stable_max_repair_required(last_build_error_paths)
                            if force_repair_write
                            else _STABLE_MAX_WRITE_REQUIRED
                        ),
                    }
                elif force_style_write and (
                    name != "write_file" or action.path != "src/app/globals.css"
                ):
                    obs = {"ok": False, "error": _STABLE_MAX_STYLE_REQUIRED}
                elif (
                    repair_mode
                    and name == "read_file"
                    and (
                        action.path not in last_build_error_paths
                        or (
                            action.path in repair_reads_since_build
                            and action.path not in repair_reread_paths
                        )
                    )
                ):
                    obs = {
                        "ok": False,
                        "error": _stable_max_repair_required(last_build_error_paths),
                    }
                elif (
                    repair_mode
                    and name == "edit_file"
                    and last_build_error_paths
                    and action.path not in last_build_error_paths
                ):
                    obs = {
                        "ok": False,
                        "error": _stable_max_repair_required(last_build_error_paths),
                    }
                elif (
                    force_entry_write
                    and name in {"write_file", "edit_file"}
                    and action.path != _STABLE_MAX_PRODUCT_ENTRY
                    and action.path in written
                ):
                    obs = {"ok": False, "error": _STABLE_MAX_SUPPORT_ADVANCE_REQUIRED}
                elif (
                    stable_max_loop
                    and _STABLE_MAX_PRODUCT_ENTRY not in written
                    and name in {"write_file", "edit_file"}
                    and action.path != _STABLE_MAX_PRODUCT_ENTRY
                    and len(written) >= _STABLE_MAX_SUPPORT_FILE_LIMIT
                ):
                    obs = {"ok": False, "error": _STABLE_MAX_ENTRY_NOW_REQUIRED}
                elif lifecycle_error:
                    obs = {"ok": False, "error": lifecycle_error}
                else:
                    tool_executed = True
                    try:
                        obs = await execute(action)
                    except Exception as exc:  # a tool crash must not kill the build
                        obs = {"ok": False, "error": f"tool {name} crashed: {exc}"}
                if (
                    tool_executed
                    and name in {"write_file", "edit_file"}
                    and obs.get("ok")
                    and action.path in written
                ):
                    post_edit_content = obs.get("content")
                    if (
                        isinstance(post_edit_content, str)
                        and post_edit_content == written[action.path]
                    ):
                        # The live MAX canary exposed a paid source-repair loop
                        # where the model returned the same ProductApp bytes,
                        # the executor reported success, and a fresh build was
                        # charged after every false edit. Preserve the existing
                        # green proof and tell the model that nothing changed.
                        obs = {"ok": False, "error": _NOOP_WRITE_REJECTED}
                        noop_write_this_turn = True
                # Emit AFTER execute so the step carries a `detail` — what the tool
                # actually did (written content preview, build output, read result)
                # — so the UI can let the user drill INTO a step and see inside it.
                if emit:
                    await emit(
                        "agent.step",
                        {
                            "step": step,
                            "action": name,
                            "path": action.path,
                            "detail": _step_detail(name, action, obs),
                            "ok": bool(obs.get("ok", True)),
                        },
                    )

                ops_this_turn += 1
                if obs.get("infra_dead"):
                    infra_this_turn += 1
                if (
                    tool_executed
                    and repair_mode
                    and name == "edit_file"
                    and not obs.get("ok")
                    and action.path in last_build_error_paths
                ):
                    # An exact edit can fail because its search text is stale or
                    # non-unique. Let the next turn reread exactly that failing
                    # file once; otherwise the edit-only guard creates a permanent
                    # dead end where every recovery read is rejected.
                    repair_reads_since_build.discard(action.path)
                    repair_reread_paths.add(action.path)
                    repair_source_cache.pop(action.path, None)
                if tool_executed and name in ("write_file", "edit_file") and obs.get("ok"):
                    repair_reread_paths.discard(action.path)
                    repair_source_cache.pop(action.path, None)
                    if isinstance(obs.get("content"), str):
                        # executor returns the post-edit content (mirrors the
                        # text loop's tracking at agent_builder.py). Prefer it
                        # for writes too: deterministic executor sanitizers can
                        # change the bytes that actually landed in the container.
                        written[action.path] = obs["content"]
                    elif name == "write_file":
                        written[action.path] = action.args.get("content", "")
                    wrote_since_build = True
                    wrote_this_turn = True
                    if force_visual_finish and action.path == "src/app/globals.css":
                        visual_finish_satisfied_this_turn = True
                    if force_source_repair:
                        source_repair_context_gap = None
                    # Tool calls in one assistant response are planned before
                    # any result is returned. Only a write from a LATER turn can
                    # have applied the visual critique.
                    if visual_feedback_step is not None and step > visual_feedback_step:
                        visual_repair_paths.add(action.path)
                    proof_after_write.clear()
                    last_green_see_step = None
                elif tool_executed and name == "build":
                    if force_visual_finish:
                        visual_finish_satisfied_this_turn = True
                    last_build_ok = bool(obs.get("ok"))
                    last_build_error_text = str(obs.get("error") or obs.get("detail") or "")
                    last_build_error_paths = (
                        frozenset()
                        if last_build_ok
                        else _typescript_repair_paths(
                            str(obs.get("error") or obs.get("detail") or ""),
                            written,
                        )
                    )
                    repair_reads_since_build.clear()
                    repair_reread_paths.clear()
                    repair_source_cache.clear()
                    repair_context_compacted = False
                    wrote_since_build = False
                elif (
                    tool_executed
                    and name == "runtime_check"
                    and not obs.get("ok")
                    and not obs.get("infra_dead")
                ):
                    # A typecheck-clean app can still fail in Next/Turbopack at
                    # request time. Treat that factual failure as repair debt;
                    # otherwise the proof gate advertises only runtime_check and
                    # rejects every attempted source fix forever.
                    last_build_ok = False
                    last_build_error_text = str(
                        obs.get("detail") or obs.get("error") or "runtime check failed"
                    )
                    last_build_error_paths = _typescript_error_paths(last_build_error_text)
                    repair_reads_since_build.clear()
                    repair_reread_paths.clear()
                    repair_source_cache.clear()
                    repair_context_compacted = False
                    wrote_since_build = False
                if (
                    tool_executed
                    and name == "see"
                    and (obs.get("needs_fix") or (obs.get("verdict") and not obs.get("ok")))
                ):
                    # Visual QA may be red because the rendered product made a
                    # failed browser request.  That is actionable product
                    # evidence even though the observation itself is ok=False;
                    # keep the exact verdict and force a focused source repair.
                    visual_feedback_step = step
                    visual_feedback_detail = str(
                        obs.get("detail") or obs.get("error") or "Visual quality is red."
                    )
                    visual_repair_paths.clear()
                    if visual_repair_attempts >= _STABLE_MAX_VISUAL_REPAIR_LIMIT:
                        visual_quality_exhausted_this_turn = True
                if obs.get("ok"):
                    successful_tools[name] = successful_tools.get(name, 0) + 1
                    if name == "generate_media" and visual_feedback_step is not None:
                        visual_media_generated_step = visual_feedback_step
                    if (
                        stable_max_loop
                        and _STABLE_MAX_PRODUCT_ENTRY not in written
                        and name in {"read_file", "list_dir", "grep", "docs"}
                    ):
                        prewrite_inspection_ops += 1
                        if name == "read_file":
                            prewrite_inspection_paths.add(action.path)
                        if prewrite_inspection_ops >= _STABLE_MAX_PREWRITE_INSPECTION_LIMIT:
                            prewrite_inspection_exhausted = True
                    if repair_mode and name == "read_file":
                        repair_reads_since_build.add(action.path)
                        repair_reread_paths.discard(action.path)
                        if isinstance(obs.get("content"), str):
                            repair_source_cache[action.path] = obs["content"]
                    if name == "read_skill":
                        skill_id = str(action.args.get("skill") or "").strip().casefold()
                        if skill_id:
                            successful_skill_ids.add(skill_id)
                        if skill_id == MAX_REQUIRED_POST_SEE_SKILL and max_lifecycle:
                            pending_visual_evaluation_step = step
                    if name in {"build", "runtime_check", "see", "probe", "verify_isolation"}:
                        if name == "see" and obs.get("needs_fix"):
                            # Actionable visual feedback was recorded above for
                            # both ok=True quality verdicts and ok=False browser
                            # failures.  Neither is production proof yet.
                            pass
                        elif name == "see" and (obs.get("proof_unavailable") or obs.get("skipped")):
                            # A fail-soft visual executor result must never satisfy
                            # a production visual-proof gate.
                            last_green_see_step = None
                            visual_proof_unavailable_this_turn = True
                        else:
                            proof_after_write.add(name)
                            if name == "see":
                                last_green_see_step = step
                _tr = _obs_to_tool_result(tu_id, obs, tool_name=name)
                if name == "build" and not obs.get("ok"):
                    _hint = _build_error_hint(str(_tr.get("content") or ""))
                    if _hint:
                        _tr["content"] = str(_tr["content"]) + _hint
                results.append(_tr)

            if force_visual_repair and wrote_this_turn:
                # A visual verdict commonly requires both component markup and
                # CSS. Live production traces showed the model applying the
                # component edits first, then being forced straight into build;
                # its next globals.css edits were rejected, so the same defects
                # survived every repair. Offer exactly one bounded stylesheet-
                # or-build turn, then resume deterministic proof. A CSS-only
                # repair is also a complete bounded attempt: the next rendered
                # verdict decides whether markup still needs work. Keeping the
                # old verdict active after a stylesheet edit traps the model in
                # an unbounded CSS -> build -> CSS loop without another ``see``.
                product_repaired = any(
                    path != "src/app/globals.css" for path in visual_repair_paths
                )
                visual_repair_attempts += 1
                visual_feedback_step = None
                visual_context_compacted_step = None
                visual_finish_pending = (
                    product_repaired and "src/app/globals.css" not in visual_repair_paths
                )
                visual_repair_paths.clear()
            elif force_visual_finish and visual_finish_satisfied_this_turn:
                visual_finish_pending = False

            if force_entry_write and _STABLE_MAX_PRODUCT_ENTRY not in written:
                results.append({"type": "text", "text": _STABLE_MAX_ENTRY_REQUIRED})

            response_hit_output_limit = (
                stable_max_loop
                and resp.get("stop_reason") == "max_tokens"
                and any(tu.get("name") in {"write_file", "edit_file"} for tu in tool_uses)
            )
            if response_hit_output_limit:
                if wrote_this_turn:
                    truncated_no_write_turns = 0
                else:
                    truncated_no_write_turns += 1
                results.append(
                    {
                        "type": "text",
                        "text": (
                            "[OUTPUT LIMIT] The previous write was truncated before its tool "
                            "arguments were complete. Do not retry the same large file. Split "
                            "the screen into smaller component files and keep the TOTAL "
                            "write_file/edit_file content in the next response below 24000 "
                            "characters. Your next turn must perform one or more smaller writes."
                        ),
                    }
                )
            else:
                truncated_no_write_turns = 0

            if done_summary is not None:
                if emit:
                    await emit("agent.done", {"step": step, "files": len(written)})
                return AgentResult(
                    done=True,
                    summary=done_summary,
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="done",
                )

            # A product-specific acceptance contract is stronger evidence than
            # one more provider turn whose only purpose is to call ``done``. Once
            # the final write has a clean build and every required proof is green,
            # finish locally. This preserves the complete app and avoids resending
            # the now-large transcript for a ceremonial final response.
            if (
                completion_check is not None
                and last_build_ok is True
                and not wrote_since_build
                and _completion_gap() is None
            ):
                if emit:
                    await emit("agent.done", {"step": step, "files": len(written)})
                return AgentResult(
                    done=True,
                    summary=(
                        "Готово — приложение полностью собрано и прошло обязательные "
                        "проверки без дополнительного запроса к модели."
                    ),
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="contract_green",
                )
            # Infra circuit breaker: generic builds retain the historical abort.
            # MAX instead waits and keeps the same durable generation alive; a
            # container/orchestrator restart must never discard completed source.
            if ops_this_turn and infra_this_turn == ops_this_turn:
                infra_dead_turns += 1
            else:
                infra_dead_turns = 0
            # EXPLORE-STALL guard (parity with run_agent_build): too many turns
            # with no successful write means the model is exploring, not
            # building. The nudge rides in the SAME user message as the
            # tool_results (roles must alternate; tool_result blocks must come
            # first), then abort as "exploring" — messages.py's honest-result
            # branches (looped-but-serves / edit-no-op) already consume it.
            if wrote_this_turn:
                no_write_turns = 0
                noop_write_turns = 0
            else:
                no_write_turns += 1
                noop_write_turns = noop_write_turns + 1 if noop_write_this_turn else 0
                _nudge_at = _STABLE_MAX_FIRST_WRITE_AT if stable_max_loop else _NO_WRITE_NUDGE_AT
                if _nudge_at <= no_write_turns and (
                    max_runtime or no_write_turns < _NO_WRITE_ABORT_AT
                ):
                    results.append(
                        {
                            "type": "text",
                            "text": (
                                (
                                    _MAX_DONE_WHEN_GREEN_NUDGE
                                    if max_runtime
                                    else _DONE_WHEN_GREEN_NUDGE
                                )
                                if last_build_ok is True and not wrote_since_build
                                else _EXPLORE_STALL_NUDGE
                            ),
                        }
                    )
                    if emit:
                        await emit("agent.stalled", {"step": step})
            if stable_max_loop and noop_write_turns >= _STABLE_MAX_NOOP_WRITE_ABORT_AT:
                log.warning(
                    "agent_native.noop_write_abort",
                    step=step,
                    consecutive_noop_turns=noop_write_turns,
                )
                return await _finish_without_provider(
                    steps=step + 1,
                    reason="noop_write",
                    detail="two consecutive source edits changed zero bytes",
                )
            if _STABLE_MAX_PRODUCT_ENTRY in written:
                turns_without_product_entry = 0
            else:
                turns_without_product_entry += 1
            convo.append({"role": "user", "content": results})
            if visual_quality_exhausted_this_turn and max_runtime:
                log.warning(
                    "agent_native.visual_quality_unmet",
                    step=step,
                    repair_attempts=visual_repair_attempts,
                )
                return AgentResult(
                    done=False,
                    summary=(
                        "Визуальная проверка всё ещё ниже production-уровня после двух "
                        "сфокусированных исправлений. Результат не опубликован, чтобы не "
                        "выдать посредственный интерфейс за готовое приложение."
                    ),
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="visual_quality_unmet",
                )
            if visual_proof_unavailable_this_turn and max_runtime and completion_check is not None:
                # ``see_page`` already performs its own bounded retry using the
                # same screenshots. Another native-agent turn would only ask the
                # provider to call ``see`` again, which previously created an
                # unbounded paid loop while producing no new product evidence.
                log.warning("agent_native.visual_proof_unavailable", step=step)
                return AgentResult(
                    done=False,
                    summary=(
                        "Визуальная проверка недоступна после повторной попытки. "
                        "Результат не отмечен как готовый; повторите генерацию."
                    ),
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="visual_proof_unavailable",
                )
            if truncated_no_write_turns >= _MAX_TRUNCATED_WRITE_ABORT_AT:
                log.warning(
                    "agent_native.oversized_write_abort",
                    step=step,
                    consecutive_truncated_turns=truncated_no_write_turns,
                )
                if emit:
                    await emit(
                        "agent.step",
                        {
                            "step": step,
                            "action": "output_limit",
                            "path": "",
                            "detail": (
                                "Две записи подряд превысили безопасный размер. Новые "
                                "платные запросы остановлены; проверяю уже записанные файлы."
                            ),
                            "ok": False,
                        },
                    )
                return await _finish_without_provider(
                    steps=step + 1,
                    reason="oversized_write",
                    detail="two consecutive write responses exceeded the output limit",
                )
            if infra_dead_turns >= _INFRA_DEAD_ABORT_AT and not max_runtime:
                log.warning("agent_native.infra_dead_abort", step=step)
                return AgentResult(
                    done=False,
                    summary="container/orchestrator unreachable — build aborted",
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="infra_error",
                )
            if infra_dead_turns >= _INFRA_DEAD_ABORT_AT and max_runtime:
                log.warning("agent_native.max_infra_reconnecting", step=step)
                await asyncio.sleep(15.0)
            if no_write_turns >= _NO_WRITE_ABORT_AT and not max_runtime:
                return AgentResult(
                    done=False,
                    summary="stuck exploring (reading/verifying) without writing any file",
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="exploring",
                )

    return await _finish_without_provider(
        steps=effective_max_steps,
        reason="max_steps",
        detail="native turn limit reached",
    )
