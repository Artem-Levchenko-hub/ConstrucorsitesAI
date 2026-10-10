"""Native structured tool-use build loop for executable app generation.

Supersedes the text-``<omnia:action>`` protocol (``agent_builder.run_agent_build``)
with native structured tool calls. Gemini 3.1 Pro Preview Custom Tools drives the
whole build end-to-end. The only "gate" is FACT-based: the ``build`` tool returns
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
import posixpath
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from yleum_api.core.config import get_settings
from yleum_api.services.agent_builder import _KNOWN_ACTIONS, Action, AgentResult
from yleum_api.services.integration_providers import PROVIDER_MAP
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NativeMessagesAttemptAuth:
    message_id: str
    project_id: str
    run_id: str
    session_id: str
    workspace_id: str
    fencing_epoch: int
    cancel_epoch: int
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class NativeProofCheckpoint:
    proof_key: str | None = None
    fast_check_green: bool = False
    source_complete: bool = False
    acceptance_started: bool = False


NativeMessagesAuthFactory = Callable[[int], Awaitable[NativeMessagesAttemptAuth]]

# Keep the proven pre-cost native loop, but route it through the current MAX
# production model instead of the retired Gemini/Opus-era default.
_MODEL = "claude-sonnet-5"
# Providers can pre-reserve the full max_tokens × output price on every call and 402
# if the key balance is below that reserve — so an over-large ceiling caps how many
# calls fit the balance (an oversized reserve can 402 mid-build,
# which surfaced to the user as "соединение потеряно"). 20000 still leaves ~12000
# tokens for tool args after the 8000 thinking budget — enough for a large file —
# while cutting the reserve ~35%. Env override: NATIVE_MAX_TOKENS (future).
_MAX_TOKENS = 20000
_THINKING_BUDGET = 8000
_MAX_TOOL_RESULT_CHARS = 20000
_HTTP_TIMEOUT_S = 300.0
# Bounded transport retry inside one turn; never restart a whole run. The budget is
# two-sided on purpose: enough attempts to ride out a flake window (see
# _CALL_RETRY_WINDOW_S), and a wall-clock ceiling so a provider that accepts the
# connection and then hangs cannot eat the whole generation deadline on one step.
_CALL_RETRIES = 7
# Retry an unexplained auth refusal only after success in this run.
# An explicit key block overrides that history: earlier access can be revoked.
_AUTH_RETRIES_AFTER_SUCCESS = 2
_AUTH_RETRY_DELAY_S = 5.0
_RUNS_WITH_A_LIVE_KEY: set[str] = set()



def _provider_complaint(response: Any) -> str:
    """Что провайдер ответил своими словами — коротко и без похожего на ключ.

    26.09.2026: прогон умер с «провайдер отклонил ключ», и понять, блокировка
    это или исчерпанный баланс, было нельзя — ответ провайдера платформа
    выбрасывала. Шлюз его как раз сохраняет, так что терялся он именно здесь.
    """

    import re

    try:
        body = response.json()
        raw = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except Exception:
        raw = None
    text = str(raw or getattr(response, "text", "") or "")[:200]
    # Похожее на ключ или токен наружу не выносим, даже если провайдер его вернул.
    return re.sub(r"[A-Za-z0-9_\-]{20,}", "…", text).strip()
# The gateway allows 240s per upstream I/O. A 210s caller window discarded
# a healthy paid response before it arrived (live 2026-10-03). Keep bounded
# response/accounting headroom; this also bounds all retries/backoff in a turn,
# and the outer editing deadline may still end the turn earlier. This is not
# a guarantee that every per-I/O gateway request completes within the window.
_CALL_RETRY_WINDOW_S = 270.0
# The first verified MAX production loop completed a five-screen product inside
# one 40-turn transcript. Keep that headroom so callers do not need a second
# provider pass with a fresh context merely because the old 30-turn clamp fired.
_HARD_MAX_STEPS = 40

# EXPLORE-STALL guard — parity with run_agent_build's no_write_streak
# (agent_builder._NO_WRITE_NUDGE_AT/_NO_WRITE_ABORT_AT = 5/14, which count single
# text-protocol ACTIONS). Native counts assistant TURNS instead — one turn often
# bundles several parallel tool calls, and the native flow legitimately spends
# its first turns surveying the big template — so the nudge fires later (6 turns
# ≈ 8-15 read calls) and the abort at 12 turns still bounds a stalled build.
_NO_WRITE_NUDGE_AT = 6
_NO_WRITE_ABORT_AT = 12
# Existing edits already have the working source in context. Allow four turns
# to locate the affected code, then two opportunities to apply a minimal patch.
_EDIT_DISCOVERY_TURNS = 4
_EDIT_NO_PROGRESS_TURNS = 6
_EDIT_SOURCE_NUDGE = (
    "EDIT SOURCE CHANGE REQUIRED: the requested edit has not changed the source tree. "
    "Apply the requested code change now using write_file or edit_file. "
    "Preserve the existing entry page, working features and unrelated files; do not rebuild "
    "the app from scratch. Stop repeated reading/checking. Then build and call done."
)
_SOURCE_REPAIR_DISCOVERY_TURNS = 2
_SOURCE_REPAIR_NO_PROGRESS_TURNS = 4
_SOURCE_REPAIR_NUDGE = (
    "SOURCE REPAIR REQUIRED: the coordinator rejected this source. A green fast check "
    "does not resolve that failure. Only an advertised one-time read_file may inspect an "
    "unread existing source; then apply a minimal write_file/edit_file change addressing "
    "the reported source error; do not repeat shell exploration or unchanged checks. "
    "Preserve working features, business data and accepted/applied migrations. "
    "Then build and hand the changed candidate to the coordinator for full verification."
)

# A brief-aware first MAX build starts from a deliberately UI-free platform
# core. Sonnet can otherwise spend the entire 12-turn stall allowance repeatedly
# reading that core (live canary 938937f7: 12 turns, 0 writes) even after the
# nudge. Once the normal discovery allowance is spent, keep the SAME transcript
# but make the environment accept implementation actions only. A successful
# write resets the regular no-write streak and unlocks every tool for repair and
# verification; infra and hard-stop guards remain unchanged.
_MAX_PREWRITE_DISCOVERY_TURNS = 6
_MAX_PRODUCT_ENTRY_PATH = "src/app/page.tsx"
_MAX_ENTRY_WRITE_GUIDANCE = (
    "Build the first runnable vertical slice from the user's brief now. Write a real, "
    "self-contained src/app/page.tsx with the product's primary screen, main user action, "
    "representative content, and required states — not a placeholder. Reuse only platform "
    "core imports that already exist; do not import product components, data modules, or "
    "styles you merely plan to create later. After this entry is written, the full toolset "
    "returns so you can extract components, add styles/data, build, and repair normally."
)
_MAX_PREWRITE_LOCK_RESULT = (
    "MAX pre-write discovery budget is exhausted. The platform core and product "
    "contract are already in context. Do not read, search, build, probe, or inspect "
    "dependencies again. " + _MAX_ENTRY_WRITE_GUIDANCE
)
_MAX_PRODUCT_ENTRY_REQUIRED_RESULT = (
    "MAX product entry is still missing. A helper, config, or standalone component "
    "is not a runnable product by itself and does not unlock more exploration. "
    + _MAX_ENTRY_WRITE_GUIDANCE
)


def _is_max_product_surface(path: str) -> bool:
    """Implementation progress includes data, server behavior and its tests.

    The entry gate separately prevents backend-only scaffolding from replacing
    the first screen. After that, ignoring backend writes aborts real full-stack
    work. Repeated identical writes and non-source notes still make no progress;
    the hard turn limit bounds semantic churn we cannot infer from paths.
    """

    normalized = _normalize_agent_path(path)
    if normalized.startswith(("src/", "tests/", "test/", "drizzle/", "migrations/")) and (
        normalized.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sql"))
    ):
        return True
    if normalized == _MAX_PRODUCT_ENTRY_PATH:
        return True
    if normalized.startswith("src/components/"):
        return True
    if normalized == "src/app/globals.css":
        return True
    if normalized.startswith("src/app/api/"):
        return False
    return normalized.startswith("src/app/") and normalized.rsplit("/", 1)[-1] in {
        "error.tsx",
        "layout.tsx",
        "loading.tsx",
        "not-found.tsx",
        "page.tsx",
    }


def _is_source_repair_path(path: str) -> bool:
    normalized = _normalize_agent_path(path)
    return (
        _is_max_product_surface(normalized)
        or (normalized.startswith("src/") and normalized.endswith((".css", ".scss")))
        or normalized in {
            ".omnia/cell.json", "package.json", "package-lock.json", "pnpm-lock.yaml",
            "tsconfig.json", "next.config.js", "next.config.mjs", "next.config.ts",
        }
    )


def _normalize_agent_path(path: str) -> str:
    normalized = (path or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _source_repair_read_path(path: str) -> str | None:
    """Canonical source-only target; never advertise config, secrets or traversal."""
    normalized = _normalize_agent_path(path)
    if (
        not normalized
        or "\x00" in normalized
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
        or not normalized.startswith(("src/", "tests/", "test/", "drizzle/", "migrations/"))
        or not normalized.endswith((
            ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sql", ".css", ".scss",
        ))
        or not _is_source_repair_path(normalized)
    ):
        return None
    return normalized


# Infra circuit breaker: consecutive turns where EVERY executed tool op died on
# infra (container/orchestrator unreachable — executor tags obs["infra_dead"]).
# 3 turns tolerates a transient orchestrator restart; a truly dead container
# aborts in ~3 turns instead of grinding the whole step budget (2026-07-08:
# hibernate stopped a container mid-build → 40 min of doomed 500 bursts).
_INFRA_DEAD_ABORT_AT = 3

# Native tool schemas — mirror the action set of make_container_executor._execute.
# `done` ends the loop. Kept intentionally minimal (fact tools only): the model
# decides everything else itself, like Claude Code.
_STR: dict[str, Any] = {"type": "string"}


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
        "provider_docs",
        "Read current public documentation from a provider's SERVER-ALLOWLISTED "
        "official HTTPS host. No credential is ever sent. Use this before wiring "
        "a requested external provider; never guess its API. Yleum/Omnia are the "
        "platform, not provider keys. For managed integrations read the local "
        "src/lib/omnia/integration-client.ts SDK instead of guessing platform docs.",
        {"provider": {"type": "string", "enum": sorted(PROVIDER_MAP)}, "query": _STR},
        ["provider", "query"],
    ),
    _tool(
        "write_file",
        "Create or overwrite a whole file with its FULL content.",
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
    _tool("bash", "Run a shell command in the dev container.", {"cmd": _STR}, ["cmd"]),
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
        "done",
        "Finish — the requested app is built AND the last build is clean. "
        "`summary` = structured RU markdown for the user (bold one-line result, then "
        "«## » sections by meaning, `code` for identifiers, lists) — see the preamble.",
        {"summary": _STR},
        ["summary"],
    ),
]

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

# MAX is a compact mobile product, not a cinematic landing page. Keep useful
# capabilities, but do not resend unavailable proof tools or landing-specific
# examples in the tool schema on every model turn.
_MAX_TOOL_DESCRIPTIONS: dict[str, str] = {
    "bash": (
        "Run a shell command INSIDE the isolated MAX project sandbox only. Use it "
        "for offline generators, tests, migrations and data transforms in the "
        "project container; it has no network and is never host/root access. To add "
        "dependencies, edit package.json; Yleum syncs them with lifecycle scripts disabled."
    ),
    "generate_media": (
        "Generate a product-relevant visual asset only when the brief requires real "
        "imagery; return a hosted URL to embed in src. Do not add decorative media "
        "that does not improve the main mobile task."
    ),
}


def _build_max_tools(
    *, allow_bash: bool, portable: bool = False, coordinator: bool = False,
) -> list[dict[str, Any]]:
    descriptions = dict(_MAX_TOOL_DESCRIPTIONS)
    if portable:
        descriptions["bash"] = (
            "Run a shell command inside this project's portable Linux machine. "
            "Root applies only inside this isolated guest, never to the host. "
            "Install required public libraries and system packages using the "
            "configured egress proxy; keep dependency manifests and lockfiles in sync. "
            "Use the project's dedicated PostgreSQL for schemas, migrations and real "
            "data; do not access platform or other projects' databases. "
            "Use lightweight checks during implementation; final build and runtime "
            "verification are managed by the finalization coordinator."
        )
    if coordinator:
        descriptions["build"] = (
            "Run bootstrap and fast source checks. Fix reported compiler/test errors. "
            "This is not the final production build; call done after source checks pass."
        )
        descriptions["done"] = (
            "Hand the completed source to the finalization coordinator after green fast checks. "
            "The coordinator performs the production build, signed runtime checks and release."
        )
    return [
        {
            **tool,
            "description": descriptions.get(tool["name"], tool["description"]),
        }
        for tool in _TOOLS
        if tool["name"] not in {"probe", "verify_isolation"}
        and (not coordinator or tool["name"] != "runtime_check")
        and (allow_bash or tool["name"] != "bash")
    ]


def _cache_toolset(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*tools[:-1], {**tools[-1], "cache_control": _CACHE}]


_MAX_TOOLS: list[dict[str, Any]] = _build_max_tools(allow_bash=False)
_MAX_TOOLS_WITH_BASH: list[dict[str, Any]] = _build_max_tools(allow_bash=True)
_MAX_TOOLS_CACHED: list[dict[str, Any]] = _cache_toolset(_MAX_TOOLS)
_MAX_TOOLS_WITH_BASH_CACHED: list[dict[str, Any]] = _cache_toolset(_MAX_TOOLS_WITH_BASH)
_MAX_PORTABLE_TOOLS_CACHED: list[dict[str, Any]] = _cache_toolset(
    _build_max_tools(allow_bash=True, portable=True)
)
_MAX_COORDINATOR_TOOLS_CACHED = _cache_toolset(
    _build_max_tools(allow_bash=False, portable=True, coordinator=True)
)
_MAX_COORDINATOR_BASH_TOOLS_CACHED = _cache_toolset(
    _build_max_tools(allow_bash=True, portable=True, coordinator=True)
)

# Once MAX discovery is exhausted, a warning alone is not a constraint: the
# provider can keep selecting read/helper tools from the full schema and the
# executor can only reject them after another paid turn. For exactly one
# recovery state, expose and force the single action that can unlock the normal
# loop. The regular toolset returns immediately after the entry is written.
_MAX_ENTRY_WRITE_TOOLS: list[dict[str, Any]] = [
    {
        **_tool(
            "write_file",
            _MAX_ENTRY_WRITE_GUIDANCE,
            {
                "path": {"type": "string", "enum": [_MAX_PRODUCT_ENTRY_PATH]},
                "content": _STR,
            },
            ["path", "content"],
        ),
        "cache_control": _CACHE,
    }
]
_MAX_ENTRY_WRITE_CHOICE: dict[str, str] = {"type": "tool", "name": "write_file"}
_SOURCE_REPAIR_TOOLS_CACHED = _cache_toolset(
    [tool for tool in _TOOLS if tool["name"] in {"write_file", "edit_file"}]
)


def _source_repair_tools(read_paths: list[str]) -> list[dict[str, Any]]:
    if not read_paths:
        return _SOURCE_REPAIR_TOOLS_CACHED
    return _cache_toolset([
        *[tool for tool in _TOOLS if tool["name"] in {"write_file", "edit_file"}],
        _tool(
            "read_file", "Inspect ONE unread existing source, then apply the targeted repair. "
            "Reading is not implementation progress and does not extend the repair budget.",
            {"path": {"type": "string", "enum": read_paths}}, ["path"],
        ),
    ])


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


def _tool_use_to_action(block: dict[str, Any], *, portable_cell: bool = False) -> Action:
    inp = block.get("input") or {}
    if not isinstance(inp, dict):
        inp = {}
    args = dict(inp)
    # Tools operate under this exact workspace root. Accept its documented
    # absolute spelling without admitting another root or traversal.
    path = args.get("path")
    if portable_cell and block.get("name") in {"list_dir", "grep"} and path == "/workspace":
        args["path"] = "."
    if (
        portable_cell
        and block.get("name") in {"read_file", "write_file", "edit_file", "list_dir", "grep"}
        and isinstance(path, str) and path.startswith("/workspace/")
    ):
        relative = path.removeprefix("/workspace/")
        if relative and ".." not in relative.replace("\\", "/").split("/"):
            args["path"] = relative
    return Action(name=str(block.get("name", "")), args=args, raw="")


def _incomplete_tool_response(resp: dict[str, Any], content: list[Any]) -> str | None:
    if resp.get("stop_reason") == "max_tokens" or resp.get("provider_finish_reason") == "length":
        return "output_limit"
    schemas = {tool["name"]: tool["input_schema"] for tool in _TOOLS}
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        args = block.get("input")
        if block.get("input_error") or not isinstance(args, dict):
            return "invalid_tool_arguments"
        # Existing done calls may omit human-facing summary; the completion
        # proof gate, not prose, authorizes finishing. It executes no tool.
        schema = {} if block.get("name") == "done" else schemas.get(block.get("name"), {})
        required = schema.get("required", [])
        if any(key not in args for key in required):
            return "missing_tool_arguments"
        properties = schema.get("properties", {})
        if any(
            args[key] is None
            or (properties.get(key, {}).get("type") == "string" and not isinstance(args[key], str))
            for key in required
        ):
            return "invalid_tool_arguments"
    return None


def _obs_to_tool_result(tool_use_id: str, obs: dict[str, Any]) -> dict[str, Any]:
    ok = bool(obs.get("ok"))
    body = obs.get("content") or obs.get("detail") or obs.get("error") or ("ok" if ok else "error")
    text = str(body)[:_MAX_TOOL_RESULT_CHARS]
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
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
    if name in ("grep", "list_dir", "bash", "read_logs", "docs", "provider_docs"):
        return _cap(obs.get("detail") or obs.get("content") or "")
    if name in ("runtime_check", "probe", "verify_isolation"):
        return _cap(obs.get("detail") or obs.get("content") or "проверка пройдена")
    return _cap(obs.get("detail") or obs.get("content") or "")


_NATIVE_COMMON_PREAMBLE = (
    "Ты — автономный инженер: строишь РАБОЧЕЕ приложение в этом проекте, как Claude "
    "Code. Инструменты вызывай напрямую: read_file/list_dir/grep — понять код, "
    "write_file/edit_file — писать, build — компиляция, bash/read_logs — рантайм, "
    "runtime_check — открыть роут в ЖИВОМ приложении, probe — реальный запрос ОТ "
    "ИМЕНИ залогиненного юзера, verify_isolation — доказать отсутствие утечки данных "
    "между юзерами, docs — свежая дока библиотек, provider_docs — актуальная "
    "официальная дока подключённого провайдера. Думай сколько нужно. Цикл: пиши "
    "код → build → чини РЕАЛЬНЫЕ ошибки до чистоты → ДОКАЖИ что работает → done. Пиши "
    "полноценно, без заглушек и TODO.\n\n"
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
)

_NATIVE_WEB_DESIGN_PREAMBLE = (
    "ВКУС В ДИЗАЙНЕ — учитывай требования продукта при написании интерфейса. "
    "Принципы (это НЕ шаблон — думай под нишу): (1) иерархия — "
    "ОДИН доминантный герой/заголовок, вторичное тише; (2) контраст ≥ 4.5:1; "
    "(3) ритм отступов кратен 4/8, секции просторные, воздух; (4) тип-шкала "
    "(крупный герой → мельче тело), не один размер; (5) НИКОГДА «голый Tailwind» "
    "дефолт (сине-серый, одинаковые карточки) — один бренд-акцент дозой; "
    "(6) mobile-first (адаптив — жёсткое условие). Исправляй конкретные дефекты, "
    "сохраняя работающие пользовательские сценарии.\n\n"
)

_NATIVE_WEB_MEDIA_PREAMBLE = (
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
)

_MAX_NATIVE_DESIGN_PREAMBLE = (
    "ВКУС В ДИЗАЙНЕ — MAX = утилитарный mobile product на 360–390px, НЕ landing page. "
    "Исправляй конкретные дефекты пользовательских сценариев. Источники правды по "
    "визуалу: `DESIGN.md`, `SYSTEM_PROMPT.md` стека и `max-ui-design.md`; они важнее "
    "общих вкусовых эвристик. Приоритет: (1) один главный пользовательский сценарий "
    "и один доминантный CTA на экран; (2) ясная иерархия, реальные loading/empty/error/"
    "success states; (3) safe-area, touch-target ≥44px, без горизонтального и "
    "вложенного scroll; (4) ритм 4/8, читаемая тип-шкала, контраст ≥4.5:1; (5) не "
    "заворачивай всё в одинаковые карточки, строй композицию размером, отступами и "
    "акцентом; (6) НЕ тащи hero-секции, Awwwards-риторику, фоновые видео, scroll-scrub "
    "и hover-first приёмы, если пользователь прямо не просил промо/immersive экран "
    "внутри mini app.\n\n"
)

_NATIVE_EXECUTION_PREAMBLE = (
    "МЕНЬШЕ БАГОВ, БЫСТРЕЕ: перед нетривиальным фиксом ДУМАЙ root-cause (не патч "
    "наугад — это плодит новые баги). Не изобретай API/SDK — `docs` (Context7) даёт "
    "РЕАЛЬНУЮ текущую сигнатуру (галлюцинация API = главный источник цикла build↔fix). "
    "Пойми минимально (read/grep) → пиши ПОЛНЫМИ файлами → build → чини реальные "
    "ошибки → ДОКАЖИ (runtime_check/probe/verify) → done. Не крути "
    "лишние read, когда контекста хватает.\n\n"
)

_NATIVE_DONE_SUMMARY_PREAMBLE = (
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

_NATIVE_PREAMBLE = (
    _NATIVE_COMMON_PREAMBLE
    + _NATIVE_WEB_DESIGN_PREAMBLE
    + _NATIVE_WEB_MEDIA_PREAMBLE
    + _NATIVE_EXECUTION_PREAMBLE
    + _NATIVE_DONE_SUMMARY_PREAMBLE
)

_MAX_NATIVE_PREAMBLE = (
    _NATIVE_COMMON_PREAMBLE
    + _MAX_NATIVE_DESIGN_PREAMBLE
    + _NATIVE_EXECUTION_PREAMBLE
    + _NATIVE_DONE_SUMMARY_PREAMBLE
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
    "[LOOP GUARD] The MAX build is clean. Run runtime_check once after the final write "
    "through the signed MAX preview, then call done NOW. Do not call "
    "generic probe or verify_isolation."
)

_MAX_NATIVE_VERIFICATION_OVERRIDE = (
    "MAX VERIFICATION OVERRIDE (takes precedence over the generic web-app rules above): "
    "MAX uses signed initData and an authenticated preview session. The generic probe and "
    "verify_isolation tools currently authenticate as a normal web user and cannot "
    "prove this runtime. Do NOT call or retry probe/verify_isolation in a MAX build. Finish "
    "the complete source product, run build until clean, run runtime_check after the final "
    "write; the executor supplies a signed MAX preview session. Fix concrete runtime "
    "failures, rerun build/runtime_check after a source change, then call done."
)
_MAX_COORDINATOR_HANDOFF = (
    "COORDINATOR HANDOFF (overrides all earlier build/runtime instructions): "
    "Implement the full requested source, data schema and tests. The build tool runs "
    "bootstrap and fast checks only. Fix its errors, then call done when the source "
    "is complete. Do not call runtime_check or wait for a running preview: production "
    "build, signed runtime proof and release happen only after your handoff. "
    "Do not claim the product is released; the coordinator decides final success."
)


def native_system_prompt(stack_guide: str, skills: str | None = None) -> str:
    """Native-tools system prompt: a short tool-loop preamble + the stack guide (+
    skills). Deliberately DROPS the text-``<omnia:action>`` LOOP_PROTOCOL — the tool
    schemas ARE the protocol now, so keeping it would only confuse a native model."""
    guide = (stack_guide or "").strip()
    is_max_prompt = "MAX PLATFORM CORE CONTRACT" in guide
    parts = [_MAX_NATIVE_PREAMBLE if is_max_prompt else _NATIVE_PREAMBLE, guide]
    if skills and skills.strip():
        parts.append(skills.strip())
    if is_max_prompt:
        parts.append(_MAX_NATIVE_VERIFICATION_OVERRIDE)
        parts.append(
            "MAX UI 0.2.0: Typography is a namespace, not a JSX component. "
            "Never render <Typography>. Inspect the installed public exports/types "
            "for the concrete text components and their props. Use the managed "
            "integration SDK for connected services; do not invent webhook secrets "
            "or local provider status mappings when the SDK supplies the capability. "
            "Before a database command, check package.json and available binaries; "
            "do not assume psql or the pg package is installed. Reuse the installed "
            "database driver and the project's documented migration command."
        )
    return "\n\n".join(p for p in parts if p)


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
    tool_choice: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    auth_factory: NativeMessagesAuthFactory | None = None,
) -> dict[str, Any]:
    """One native /v1/messages call with 429 (concurrency) retry. Returns the parsed
    Anthropic response dict, or raises the last error."""
    import asyncio

    payload: dict[str, Any] = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "thinking": {"type": "enabled", "budget_tokens": _THINKING_BUDGET},
        # Prompt caching: cache the stable system prompt + tool schemas, and a
        # moving breakpoint on the transcript tail (see _with_incremental_cache).
        "system": _system_blocks(system),
        "tools": tools if tools is not None else _TOOLS_CACHED,
        "tool_choice": dict(tool_choice) if tool_choice is not None else {"type": "auto"},
        "messages": _with_incremental_cache(convo),
    }
    if user_id:
        payload["user"] = user_id
    last: Exception | None = None
    auth_attempt = 0
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    for attempt in range(_CALL_RETRIES):
        if loop.time() - started_at >= _CALL_RETRY_WINDOW_S:
            break
        request_headers = dict(headers) if headers is not None else None
        metadata: dict[str, Any] = {
            "user_id": user_id,
            "project_id": project_id,
            "run_id": run_id,
            "message_id": message_id,
            "free": free,
            "stage": stage,
            "retry_count": attempt,
        }
        if auth_factory is not None:
            auth = await auth_factory(attempt)
            request_headers = dict(auth.headers)
            metadata.update(
                project_id=auth.project_id,
                run_id=auth.run_id,
                session_id=auth.session_id,
                workspace_id=auth.workspace_id,
                fencing_epoch=auth.fencing_epoch,
                cancel_epoch=auth.cancel_epoch,
                message_id=auth.message_id,
            )
        # Every provider attempt is attributable. The gateway persists these
        # fields with the provider usage row, including a successful retry.
        payload["metadata"] = metadata
        # Exponential with a cap: back off harder from a provider that is already
        # struggling, but never spend a whole minute inside one pause.
        delay = min(45.0, 4.0 * (2**attempt))
        try:
            remaining = _CALL_RETRY_WINDOW_S - (loop.time() - started_at)
            if remaining <= 0:
                break
            # httpx's timeout is per I/O operation. The outer scope also bounds
            # an active request by total remaining wall time; no retry follows
            # an exhausted request whose upstream accounting may still settle.
            async with asyncio.timeout(remaining):
                r = await client.post(
                    url,
                    json=payload,
                    timeout=min(_HTTP_TIMEOUT_S, remaining),
                    headers=request_headers,
                )
            if r.status_code == 503:
                try:
                    error_body = r.json()
                except ValueError:
                    error_body = None
                billing_error = error_body.get("error") if isinstance(error_body, dict) else None
                if isinstance(billing_error, dict) and (
                    billing_error.get("type") == "billing_unavailable"
                    or billing_error.get("code") == "billing_unavailable"
                ):
                    # The provider may already have completed this call. Retrying
                    # before accounting settles can incur another paid request.
                    raise RuntimeError(
                        "BILLING_UNAVAILABLE: Не удалось подтвердить учёт расходов. "
                        "Автоматический повтор остановлен, чтобы избежать повторных затрат."
                    )
            if r.status_code == 409:
                try:
                    route_error = r.json().get("error", {}).get("type")
                except (ValueError, AttributeError):
                    route_error = None
                if route_error == "model_route_mismatch":
                    raise RuntimeError(
                        "PROVIDER_MODEL_MISMATCH: upstream model identity is unsupported; "
                        "no automatic replay or wrong-model charge"
                    )
            # 402 = provider key out of balance. Retrying can't fix it, so fail
            # FAST with a human cause instead of grinding 8 backoff retries and
            # surfacing an opaque "соединение потеряно" 3+ minutes later.
            if r.status_code in {401, 403}:
                proven = bool(run_id) and str(run_id) in _RUNS_WITH_A_LIVE_KEY
                complaint = _provider_complaint(r)
                blocked = "key is blocked" in complaint.casefold()
                if blocked:
                    guidance = (
                        "; провайдер сообщил о блокировке ключа. "
                        "Разблокируйте его у провайдера перед новым запуском."
                    )
                elif proven:
                    guidance = (
                        "; в этом запуске ключ раньше работал, но сейчас провайдер "
                        "отказывает в доступе. Проверьте статус и разрешения ключа."
                    )
                else:
                    guidance = "; проверьте блокировку и разрешения ключа."
                refusal = RuntimeError(
                    "PROVIDER_AUTH_FAILED: провайдер модели отклонил ключ доступа"
                    + guidance
                    + (f" Ответ провайдера: {complaint}" if complaint else "")
                )
                if proven and not blocked and auth_attempt < _AUTH_RETRIES_AFTER_SUCCESS:
                    # Keep the auth cause if the shared retry budget ends first.
                    auth_attempt += 1
                    last = refusal
                    await asyncio.sleep(min(
                        _AUTH_RETRY_DELAY_S * auth_attempt,
                        max(0.0, _CALL_RETRY_WINDOW_S - (loop.time() - started_at)),
                    ))
                    continue
                raise refusal
            if r.status_code == 402:
                complaint = _provider_complaint(r)
                raise RuntimeError(
                    "PAYMENT_REQUIRED: баланс LLM-провайдера (LLMGW) исчерпан — "
                    "пополни ключ и повтори промпт"
                    + (f" Ответ провайдера: {complaint}" if complaint else "")
                )
            if r.status_code == 429 or (r.status_code >= 400 and "rate_limit" in r.text[:300]):
                last = RuntimeError(f"429 concurrency (attempt {attempt + 1})")
                delay = 6.0 * (attempt + 1)
            else:
                r.raise_for_status()
                body = r.json()
                if not isinstance(body, dict):
                    raise RuntimeError("messages API returned a non-object payload")
                if run_id:
                    # Набор живёт в долгом процессе воркера — держим его маленьким.
                    if len(_RUNS_WITH_A_LIVE_KEY) > 512:
                        _RUNS_WITH_A_LIVE_KEY.clear()
                    _RUNS_WITH_A_LIVE_KEY.add(str(run_id))
                return body
        except TimeoutError as exc:
            raise RuntimeError("PROVIDER_TIMEOUT: native request budget exhausted") from exc
        except httpx.ReadTimeout as exc:
            # The gateway may still finish/account the accepted upstream call.
            # Replaying it could pay twice while the original response settles.
            raise RuntimeError("PROVIDER_TIMEOUT: native response read timed out") from exc
        except httpx.HTTPError as exc:
            # The provider flakes in SUSTAINED bursts. Live on 2026-09-22 a plain
            # 502 window of 51 s was enough to fail a user's whole build, because
            # three attempts only covered ~28 s of backoff while the comment here
            # claimed minutes. The budget now really spans the window it promises.
            last = exc
        # A pause is only worth taking if another attempt follows it.
        if attempt + 1 >= _CALL_RETRIES or loop.time() - started_at >= _CALL_RETRY_WINDOW_S:
            break
        await asyncio.sleep(min(
            delay, max(0.0, _CALL_RETRY_WINDOW_S - (loop.time() - started_at)),
        ))
    raise last or RuntimeError("messages call failed")


async def _run_native_segment(
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
    max_steps: int = 24,
    allow_max_bash: bool = False,
    portable_cell: bool = False,
    messages_url: str | None = None,
    messages_headers: Mapping[str, str] | None = None,
    messages_auth_factory: NativeMessagesAuthFactory | None = None,
    initial_files: Mapping[str, str] | None = None,
    edit_deadline: datetime | None = None,
    source_repair: bool = False,
    edit_source_changed: Callable[[], bool] | None = None,
) -> AgentResult:
    """Drive the native tool-use loop until the model calls ``done`` (with a clean
    build) or the step budget is hit. Returns the written files + transcript.

    ``system`` is the stack/system prompt (reuse ``agent_builder.build_system_prompt``);
    ``task`` is the user's request. One model, full transcript (thinking preserved),
    fact-gate = the ``build`` tool. No lossy window — instead the full prefix
    (system + tools + transcript) rides Anthropic prompt caching every turn, so
    resending it is ~90% cheaper than a fresh write (see _call_messages).
    """
    settings = get_settings()
    url = messages_url or f"{settings.llm_gateway_url.rstrip('/')}/v1/messages"

    convo: list[dict[str, Any]] = [{"role": "user", "content": task}]
    baseline_files = dict(initial_files or {})
    repair_read_paths = {
        canonical for path in baseline_files
        if (canonical := _source_repair_read_path(path)) is not None
    } if source_repair else set()
    repair_read_attempts: set[str] = set()
    repair_targeted_read_used = False
    written: dict[str, str] = {}
    last_build_ok: bool | None = None
    wrote_since_build = False
    edit_tool_choice_ignored = False
    # Consecutive turns without a visible/product-surface change. A helper or
    # config mutation still dirties the build fact-gate, but is not progress.
    no_write_turns = 0
    non_entry_writes_before_entry = 0
    infra_dead_turns = 0  # consecutive turns where EVERY tool op died on infra
    successful_tools: dict[str, int] = {}
    proof_after_write: set[str] = set()
    invalidated_proofs: set[str] = set()
    checkpoint_at: float | None = None
    incomplete_responses = 0
    unverified_response_problem: str | None = None

    effective_max_steps = min(_HARD_MAX_STEPS, max(1, int(max_steps)))
    max_runtime = "MAX VERIFICATION OVERRIDE" in system
    coordinated_max = max_runtime and portable_cell and settings.use_max_finalization_coordinator
    if coordinated_max:
        system += "\n\n" + _MAX_COORDINATOR_HANDOFF

    def _evidence() -> dict[str, int]:
        result = dict(successful_tools)
        for tool in invalidated_proofs:
            result[f"{tool}_after_write"] = 0
        for tool in proof_after_write:
            result[f"{tool}_after_write"] = 1
        return result

    def _invalidate_proofs(*names: str) -> None:
        invalidated_proofs.update(names)
        proof_after_write.difference_update(names)

    def _repair_source_changed() -> bool:
        return any(
            _is_source_repair_path(path) and content != baseline_files.get(path, "")
            for path, content in written.items()
        )

    def _remaining_repair_reads() -> list[str]:
        if repair_targeted_read_used:
            return []
        return sorted(repair_read_paths - repair_read_attempts)

    def _record_turn_progress(product_progress: bool) -> None:
        nonlocal no_write_turns
        if edit_source_changed is not None and not edit_source_changed():
            no_write_turns += 1
        elif source_repair and not _repair_source_changed():
            no_write_turns += 1
        elif product_progress:
            no_write_turns = 0
        else:
            no_write_turns += 1

    def _completion_gap() -> str | None:
        if edit_source_changed is not None and not edit_source_changed():
            return _EDIT_SOURCE_NUDGE
        if source_repair and not _repair_source_changed():
            return _SOURCE_REPAIR_NUDGE
        if completion_check is None:
            return None
        try:
            return completion_check(written, _evidence())
        except Exception as exc:
            log.exception("agent_native.completion_check_failed")
            return f"Product acceptance check failed: {type(exc).__name__}."

    async def _finish_without_provider(*, steps: int, reason: str, detail: str) -> AgentResult:
        """Stop provider traffic; flagged MAX transfers proof to finalization."""
        if edit_source_changed is not None and not edit_source_changed():
            return AgentResult(
                done=False, summary=_EDIT_SOURCE_NUDGE, files=written, steps=steps,
                transcript=convo, stop_reason="no_progress", evidence=_evidence(),
            )
        fast_check_green = False
        if coordinated_max and unverified_response_problem == "output_limit":
            # Preserve the provider cause across step/reserve exhaustion. The
            # rejected batch contributed no actions, but prior applied source
            # can still be checked locally within the finalization reserve.
            reason = "output_limit"
            if written:
                try:
                    checked = await execute(Action("build", {}, ""))
                except Exception as exc:
                    raise_if_terminal_cell_error(exc)
                    # Deadline/check failures must not promote a candidate or
                    # expose exception/provider internals in the user summary.
                    checked = {"ok": False}
                if checked.get("environment_mutated"):
                    _invalidate_proofs("build", "runtime_check", "probe", "verify_isolation")
                fast_check_green = bool(checked.get("ok"))
                if fast_check_green:
                    successful_tools["build"] = successful_tools.get("build", 0) + 1
                    proof_after_write.add("build")
                if emit:
                    await emit("agent.step", {
                        "step": steps, "action": "build", "path": "",
                        "detail": _step_detail("build", Action("build", {}, ""), checked),
                        "ok": fast_check_green, "reason": "output_limit",
                    })
        if incomplete_responses and last_build_ok is not True and not fast_check_green:
            return AgentResult(
                done=False, summary=(
                    "Модель достигла лимита ответа. Текущий кандидат не прошёл обязательную "
                    "проверку; сохранён предыдущий результат."
                    if reason == "output_limit" else
                    "Неполный ответ модели не был исправлен и проверен. "
                    "Сохранён предыдущий результат."
                ), files=written, steps=steps, transcript=convo,
                stop_reason="output_limit" if reason == "output_limit" else "error",
                evidence=_evidence(),
            )
        if max_runtime and get_settings().use_max_finalization_coordinator:
            from yleum_api.services.max_generation_contract import (
                max_source_completion_gap,
            )

            effective_files = {**baseline_files, **written}
            source_gap = (
                _completion_gap() if completion_check is not None else max_source_completion_gap(
                    task, effective_files, portable=".omnia/cell.json" in effective_files,
                )
            )
            source_complete = source_gap is None
            return AgentResult(
                done=False,
                summary=(
                    "Исходники собраны; выполняю финальную проверку."
                    if source_complete
                    else source_gap or detail
                ),
                files=written,
                steps=steps,
                transcript=convo,
                stop_reason="max_steps" if reason == "max_steps" else reason,
                evidence=_evidence(),
                needs_finalization=source_complete,
                proof_checkpoint=NativeProofCheckpoint(
                    fast_check_green=fast_check_green,
                    source_complete=source_complete,
                    acceptance_started=source_complete,
                ),
            )
        _invalidate_proofs("build")
        try:
            final_build = await execute(Action(name="build", args={}, raw=""))
        except Exception as exc:
            raise_if_terminal_cell_error(exc)
            final_build = {"ok": False, "error": f"final build probe crashed: {exc}"}
        if final_build.get("environment_mutated"):
            _invalidate_proofs("build", "runtime_check", "probe", "verify_isolation")
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
                if action is None:
                    break
                try:
                    proof = await execute(action)
                except Exception as exc:
                    raise_if_terminal_cell_error(exc)
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
                    evidence=_evidence(),
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
                evidence=_evidence(),
            )
        return AgentResult(
            done=False,
            summary=str(final_build.get("detail") or final_build.get("error") or detail),
            files=written,
            steps=steps,
            transcript=convo,
            stop_reason=f"{reason}_red",
            evidence=_evidence(),
        )

    async with httpx.AsyncClient() as client:
        for step in range(effective_max_steps):
            remaining = (
                (edit_deadline - datetime.now(UTC)).total_seconds()
                if edit_deadline is not None else None
            )
            if remaining is not None and remaining <= 0:
                return await _finish_without_provider(
                    steps=step, reason="finalization_reserve",
                    detail="Source editing budget ended; final checks remain mandatory.",
                )
            # Check the first source batch, then at most once per three minutes
            # while it changes. Compiler feedback must reach the model while it
            # still has time to repair, rather than after twenty minutes of work.
            now = asyncio.get_running_loop().time()
            if coordinated_max and wrote_since_build and (
                edit_source_changed is None or edit_source_changed()
            ) and (
                checkpoint_at is None or now - checkpoint_at >= 180
            ):
                checked = await execute(Action("build", {}, ""))
                checkpoint_at = asyncio.get_running_loop().time()
                last_build_ok = bool(checked.get("ok"))
                wrote_since_build = False
                _invalidate_proofs("build")
                if last_build_ok:
                    unverified_response_problem = None
                    proof_after_write.add("build")
                    successful_tools["build"] = successful_tools.get("build", 0) + 1
                detail = _step_detail("build", Action("build", {}, ""), checked)
                convo.append({"role": "user", "content": "EARLY SOURCE CHECK:\n" + detail})
                if emit:
                    await emit("agent.step", {
                        "step": step, "action": "build", "path": "",
                        "detail": detail, "ok": last_build_ok,
                    })
                if edit_deadline is not None:
                    remaining = (edit_deadline - datetime.now(UTC)).total_seconds()
                    if remaining <= 0:
                        return await _finish_without_provider(
                            steps=step, reason="finalization_reserve", detail=detail,
                        )
            if remaining is not None and remaining <= 180:
                convo.append({"role": "user", "content": (
                    f"Editing time remaining: {int(remaining)} seconds. Complete the requested "
                    "source now, fix known errors and call done. Final production checks "
                    "have reserved time; do not start new exploration or optional features."
                )})
            force_max_entry_write = (
                max_runtime
                and completion_check is not None
                and _MAX_PRODUCT_ENTRY_PATH not in baseline_files
                and _MAX_PRODUCT_ENTRY_PATH not in written
                and (
                    no_write_turns >= _MAX_PREWRITE_DISCOVERY_TURNS
                    or non_entry_writes_before_entry > 0
                )
            )
            repair_write_required = (
                source_repair and not _repair_source_changed()
                and no_write_turns >= _SOURCE_REPAIR_DISCOVERY_TURNS
            )
            edit_write_required = (
                edit_source_changed is not None and not edit_source_changed()
                and no_write_turns >= _EDIT_DISCOVERY_TURNS
            )
            call_stage = (
                "build_plan"
                if step == 0
                else "verification"
                if last_build_ok is True and not wrote_since_build
                else "native_agent"
            )
            call_budget = asyncio.timeout(remaining)
            try:
                async with call_budget:
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
                        headers=messages_headers,
                        auth_factory=messages_auth_factory,
                        tools=(
                            _SOURCE_REPAIR_TOOLS_CACHED
                            if edit_write_required
                            else _source_repair_tools(_remaining_repair_reads())
                            if repair_write_required
                            else _MAX_ENTRY_WRITE_TOOLS
                            if force_max_entry_write
                            else _MAX_COORDINATOR_BASH_TOOLS_CACHED
                            if coordinated_max and allow_max_bash
                            else _MAX_COORDINATOR_TOOLS_CACHED
                            if coordinated_max
                            else _MAX_PORTABLE_TOOLS_CACHED
                            if max_runtime and allow_max_bash and portable_cell
                            else _MAX_TOOLS_WITH_BASH_CACHED
                            if max_runtime and allow_max_bash
                            else _MAX_TOOLS_CACHED
                            if max_runtime
                            else None
                        ),
                        tool_choice=(
                            {"type": "tool", "name": "edit_file"}
                            if edit_write_required and edit_tool_choice_ignored
                            else {"type": "any"} if repair_write_required or edit_write_required
                            else _MAX_ENTRY_WRITE_CHOICE if force_max_entry_write else None
                        ),
                    )
            except Exception as exc:
                if isinstance(exc, TimeoutError) and call_budget.expired():
                    return await _finish_without_provider(
                        steps=step, reason="finalization_reserve",
                        detail="Editing budget exhausted during the provider call.",
                    )
                # A provider failure is not a model handoff to verification.
                # Do not build/accept a starter or overwrite the primary cause
                # with a source-completion gap after failed model traffic.
                safe_reason = (
                    str(exc) if str(exc).startswith((
                        "PROVIDER_AUTH_FAILED", "PAYMENT_REQUIRED", "BILLING_UNAVAILABLE",
                    ))
                    else "PROVIDER_UNAVAILABLE: вызов модели не завершился; повторите позже."
                )
                reason_code = safe_reason.partition(":")[0].lower()
                if str(exc).startswith("PROVIDER_TIMEOUT:"):
                    reason_code = "provider_timeout"
                    safe_reason = (
                        "PROVIDER_TIMEOUT: провайдер модели не ответил вовремя. "
                        "Автоматический повтор остановлен."
                    )
                log.warning(
                    "agent_native.provider_failed", error_type=type(exc).__name__,
                    reason_code=reason_code,
                    run_id=str(run_id) if run_id else None,
                    project_id=str(project_id) if project_id else None,
                    message_id=str(message_id) if message_id else None,
                    stage=call_stage,
                )
                return AgentResult(
                    done=False, summary=safe_reason, files=written, steps=step,
                    transcript=convo, stop_reason="provider_error", evidence=_evidence(),
                )

            content = resp.get("content")
            if not isinstance(content, list):
                return AgentResult(
                    done=False,
                    summary="malformed upstream (no content list)",
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="error",
                    evidence=_evidence(),
                )
            if (
                edit_write_required and not source_repair and not edit_tool_choice_ignored
                and _MAX_PRODUCT_ENTRY_PATH in baseline_files
            ):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    if name in ("write_file", "edit_file"):
                        continue
                    edit_tool_choice_ignored = True
                    mismatch = {
                        "tool": (
                            name if isinstance(name, str) and name in _KNOWN_ACTIONS else "unknown"
                        ),
                        "step": step,
                        "required_mode": "existing_edit_write_only",
                    }
                    log.warning("agent_native.edit_tool_contract_mismatch", **mismatch)
                    if emit:
                        await emit("agent.tool_contract_mismatch", mismatch)
                    break
            response_problem = _incomplete_tool_response(resp, content)
            if response_problem:
                # Validate the entire response before executing ANY call. A valid
                # prefix of a truncated batch is not an authorized complete plan.
                # Never replay earlier successful calls or echo broken/raw JSON.
                incomplete_responses += 1
                unverified_response_problem = response_problem
                # A rejected verification/done response cannot reuse an earlier
                # green checkpoint as completion of the requested change.
                last_build_ok = None
                _invalidate_proofs("build", "runtime_check", "probe", "verify_isolation")
                feedback = (
                    f"Provider response rejected ({response_problem}). "
                    "No tools from this response were executed. Earlier successful "
                    "tool calls remain applied; do not repeat them. Use one small "
                    "edit_file change (path, search, replace) per response, "
                    "or split new code into small modules "
                    "with write_file. Do not rewrite the whole large page. Supply complete "
                    "JSON arguments including all required fields, then build and verify."
                )
                sanitized = [
                    {"type": "tool_use", "id": block.get("id", ""),
                     "name": block.get("name", ""), "input": {}}
                    if isinstance(block, dict) and block.get("type") == "tool_use" else block
                    for block in content
                ]
                convo.append({"role": "assistant", "content": sanitized})
                recovery_results = [
                    {"type": "tool_result", "tool_use_id": block.get("id", ""),
                     "is_error": True, "content": feedback}
                    for block in sanitized
                    if isinstance(block, dict) and block.get("type") == "tool_use"
                ]
                convo.append({"role": "user", "content": [
                    *recovery_results, {"type": "text", "text": feedback},
                ]})
                if emit:
                    await emit("agent.step", {
                        "step": step, "action": "provider_response", "ok": False,
                        "detail": feedback, "reason": response_problem,
                        "recovery_attempt": incomplete_responses,
                    })
                if incomplete_responses > 2:
                    if coordinated_max and response_problem == "output_limit":
                        return await _finish_without_provider(
                            steps=step + 1, reason="output_limit", detail=feedback,
                        )
                    return AgentResult(
                        done=False, summary="Модель повторно вернула неполные команды. "
                        "Изменения из этих ответов не выполнялись; сохранён предыдущий результат.",
                        files=written, steps=step + 1, transcript=convo,
                        stop_reason="error", evidence=_evidence(),
                    )
                if edit_source_changed is not None:
                    # A completed paid response still spent an unchanged edit
                    # turn when its incomplete tool batch could not execute.
                    _record_turn_progress(False)
                    if not edit_source_changed():
                        if no_write_turns >= _EDIT_NO_PROGRESS_TURNS:
                            return await _finish_without_provider(
                                steps=step + 1, reason="exploring", detail=_EDIT_SOURCE_NUDGE,
                            )
                        if no_write_turns >= _EDIT_DISCOVERY_TURNS:
                            convo[-1]["content"].append({
                                "type": "text", "text": _EDIT_SOURCE_NUDGE,
                            })
                continue
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
                # Prose is not proof. Keep the turn inside the same hard budget
                # until a real clean build exists; never ship a broken app because
                # the model happened to finish speaking.
                gap = _completion_gap() if _done and (
                    last_build_ok is True or edit_source_changed is not None
                ) else None
                if _done and last_build_ok is True and not wrote_since_build and not gap:
                    return AgentResult(
                        done=True,
                        summary=_text or "Готово — приложение собрано и проверено.",
                        files=written,
                        steps=step + 1,
                        transcript=convo,
                        stop_reason="done_green",
                        evidence=_evidence(),
                    )
                if edit_source_changed is not None:
                    _record_turn_progress(False)
                elif source_repair:
                    _record_turn_progress(False)
                    if not _repair_source_changed():
                        gap = _SOURCE_REPAIR_NUDGE
                        if no_write_turns >= _SOURCE_REPAIR_DISCOVERY_TURNS and emit:
                            await emit("agent.stalled", {"step": step, "reason": "source_repair"})
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
                if edit_source_changed is not None and not edit_source_changed():
                    if no_write_turns >= _EDIT_NO_PROGRESS_TURNS:
                        return await _finish_without_provider(
                            steps=step + 1, reason="exploring", detail=_EDIT_SOURCE_NUDGE,
                        )
                if (
                    source_repair and not _repair_source_changed()
                    and no_write_turns >= _SOURCE_REPAIR_NO_PROGRESS_TURNS
                ):
                    return await _finish_without_provider(
                        steps=step + 1, reason="exploring", detail=_SOURCE_REPAIR_NUDGE,
                    )
                continue

            results: list[dict[str, Any]] = []
            done_summary: str | None = None
            product_progress_this_turn = False
            ops_this_turn = 0  # executed (non-done) tool ops this turn
            infra_this_turn = 0  # of those, how many died on infra
            for tu in tool_uses:
                name = tu.get("name", "")
                tu_id = tu.get("id", "")
                if name == "done":
                    if edit_source_changed is not None and not edit_source_changed():
                        results.append({
                            "type": "tool_result", "tool_use_id": tu_id,
                            "is_error": True, "content": "Not done yet: " + _EDIT_SOURCE_NUDGE,
                        })
                        continue
                    # Fact-gate: refuse a premature done if the model wrote files but
                    # never confirmed a CLEAN build afterwards. Bounded (R-10).
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

                action = _tool_use_to_action(tu, portable_cell=portable_cell)
                _max_contract_active = max_runtime and completion_check is not None
                _max_entry_missing = (
                    _MAX_PRODUCT_ENTRY_PATH not in baseline_files
                    and _MAX_PRODUCT_ENTRY_PATH not in written
                )
                _max_prewrite_locked = (
                    _max_contract_active
                    and _max_entry_missing
                    and no_write_turns >= _MAX_PREWRITE_DISCOVERY_TURNS
                )
                _max_entry_required = (
                    _max_contract_active
                    and _max_entry_missing
                    and (_max_prewrite_locked or non_entry_writes_before_entry > 0)
                )
                obs: dict[str, Any]
                if name not in _KNOWN_ACTIONS:
                    obs = {"ok": False, "error": f"unknown action {name}"}
                elif edit_source_changed is not None and not edit_source_changed() and (
                    name in {"build", "runtime_check", "probe", "verify_isolation"}
                    or (edit_write_required and name not in {"write_file", "edit_file"})
                ):
                    obs = {"ok": False, "error": _EDIT_SOURCE_NUDGE}
                elif repair_write_required and not _repair_source_changed() and not (
                    (name in {"write_file", "edit_file"} and _is_source_repair_path(action.path))
                    or (
                        name == "read_file"
                        and _source_repair_read_path(action.path) in _remaining_repair_reads()
                    )
                ):
                    obs = {"ok": False, "error": _SOURCE_REPAIR_NUDGE}
                elif coordinated_max and name == "runtime_check":
                    obs = {"ok": False, "error": _MAX_COORDINATOR_HANDOFF}
                elif _max_entry_required and not (
                    name in {"write_file", "edit_file"}
                    and _normalize_agent_path(action.path) == _MAX_PRODUCT_ENTRY_PATH
                ):
                    obs = {
                        "ok": False,
                        "error": (
                            _MAX_PREWRITE_LOCK_RESULT
                            if _max_prewrite_locked and non_entry_writes_before_entry == 0
                            else _MAX_PRODUCT_ENTRY_REQUIRED_RESULT
                        ),
                    }
                else:
                    if source_repair and name == "read_file":
                        # Discovery still uses the executor's broader relative-path
                        # grammar. Count equivalent spellings of a read once, without
                        # admitting those spellings through the strict locked schema.
                        read_path = _source_repair_read_path(
                            posixpath.normpath(action.path.replace("\\", "/")),
                        )
                        if read_path is not None:
                            repair_read_attempts.add(read_path)
                        if repair_write_required and not _repair_source_changed():
                            # Consume before execution, including failures and additional
                            # tools in this batch; never reset no-write/proof budgets.
                            repair_targeted_read_used = True
                    if name in {"build", "runtime_check", "probe", "verify_isolation"}:
                        _invalidate_proofs(name)
                    try:
                        obs = await execute(action)
                    except Exception as exc:  # a tool crash must not kill the build
                        raise_if_terminal_cell_error(exc)
                        obs = {"ok": False, "error": f"tool {name} crashed: {exc}"}
                if obs.get("environment_mutated"):
                    _invalidate_proofs("build", "runtime_check", "probe", "verify_isolation")
                    wrote_since_build = True
                    last_build_ok = None
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
                if name in ("write_file", "edit_file") and obs.get("ok"):
                    normalized_path = _normalize_agent_path(action.path)
                    tracked_path = normalized_path if _max_contract_active else action.path
                    previous_content = written.get(
                        tracked_path,
                        baseline_files.get(tracked_path),
                    )
                    next_content: str | None = None
                    if name == "write_file":
                        next_content = action.args.get("content", "")
                    elif isinstance(obs.get("content"), str):
                        # executor returns the post-edit content (mirrors the
                        # text loop's tracking at agent_builder.py) — closes the
                        # gap where edit_file never dirtied the done fact-gate.
                        next_content = obs["content"]
                    if next_content is not None:
                        written[tracked_path] = next_content
                    wrote_since_build = True
                    _invalidate_proofs("build", "runtime_check", "probe", "verify_isolation")
                    content_changed = next_content is None or next_content != previous_content
                    if _max_contract_active:
                        if (
                            _MAX_PRODUCT_ENTRY_PATH not in baseline_files
                            and _MAX_PRODUCT_ENTRY_PATH not in written
                            and normalized_path != _MAX_PRODUCT_ENTRY_PATH
                        ):
                            non_entry_writes_before_entry += 1
                        if content_changed and _is_max_product_surface(normalized_path):
                            product_progress_this_turn = True
                    elif content_changed:
                        product_progress_this_turn = True
                if isinstance(obs.get("files"), dict) and obs.get("files"):
                    _invalidate_proofs("build", "runtime_check", "probe", "verify_isolation")
                    wrote_since_build = True
                    _shell_changed = False
                    for _raw_path, _raw_content in obs["files"].items():
                        if not isinstance(_raw_path, str) or not isinstance(_raw_content, str):
                            continue
                        normalized_path = _normalize_agent_path(_raw_path)
                        tracked_path = normalized_path
                        previous_content = written.get(
                            tracked_path,
                            baseline_files.get(tracked_path),
                        )
                        written[tracked_path] = _raw_content
                        content_changed = previous_content != _raw_content
                        _shell_changed = _shell_changed or content_changed
                        if _max_contract_active:
                            if (
                                _MAX_PRODUCT_ENTRY_PATH not in baseline_files
                                and _MAX_PRODUCT_ENTRY_PATH not in written
                                and normalized_path != _MAX_PRODUCT_ENTRY_PATH
                            ):
                                non_entry_writes_before_entry += 1
                            if content_changed and _is_max_product_surface(normalized_path):
                                product_progress_this_turn = True
                        elif content_changed:
                            product_progress_this_turn = True
                    if (
                        not product_progress_this_turn
                        and _shell_changed
                        and not _max_contract_active
                    ):
                        product_progress_this_turn = True
                elif name == "build":
                    last_build_ok = bool(obs.get("ok"))
                    wrote_since_build = False
                    if last_build_ok:
                        unverified_response_problem = None
                if obs.get("ok"):
                    successful_tools[name] = successful_tools.get(name, 0) + 1
                    if name in {"build", "runtime_check", "probe", "verify_isolation"}:
                        proof_after_write.add(name)
                _tr = _obs_to_tool_result(tu_id, obs)
                if name == "build" and not obs.get("ok"):
                    _hint = _build_error_hint(str(_tr.get("content") or ""))
                    if _hint:
                        _tr["content"] = str(_tr["content"]) + _hint
                results.append(_tr)

            if (
                max_runtime
                and completion_check is not None
                and _MAX_PRODUCT_ENTRY_PATH not in baseline_files
                and _MAX_PRODUCT_ENTRY_PATH not in written
                and non_entry_writes_before_entry > 0
                and _MAX_PRODUCT_ENTRY_REQUIRED_RESULT not in str(results)
            ):
                results.append({"type": "text", "text": _MAX_PRODUCT_ENTRY_REQUIRED_RESULT})

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
                    evidence=_evidence(),
                )
            # Infra circuit breaker: a turn where EVERY executed op died on
            # infra means the container/orchestrator is gone — the model can't
            # fix that. Abort after a few such turns instead of grinding the
            # whole step budget against a corpse (2026-07-08 incident).
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
            _record_turn_progress(product_progress_this_turn)
            if edit_source_changed is not None and not edit_source_changed():
                if no_write_turns >= _EDIT_DISCOVERY_TURNS:
                    results.append({"type": "text", "text": _EDIT_SOURCE_NUDGE})
                if no_write_turns >= _EDIT_NO_PROGRESS_TURNS:
                    convo.append({"role": "user", "content": results})
                    return await _finish_without_provider(
                        steps=step + 1, reason="exploring", detail=_EDIT_SOURCE_NUDGE,
                    )
            if no_write_turns:
                if (
                    source_repair and not _repair_source_changed()
                    and no_write_turns >= _SOURCE_REPAIR_DISCOVERY_TURNS
                ):
                    results.append({"type": "text", "text": _SOURCE_REPAIR_NUDGE})
                    if emit:
                        await emit("agent.stalled", {"step": step, "reason": "source_repair"})
                elif _NO_WRITE_NUDGE_AT <= no_write_turns < _NO_WRITE_ABORT_AT:
                    max_entry_write_required = (
                        max_runtime
                        and completion_check is not None
                        and _MAX_PRODUCT_ENTRY_PATH not in baseline_files
                        and _MAX_PRODUCT_ENTRY_PATH not in written
                        and no_write_turns >= _MAX_PREWRITE_DISCOVERY_TURNS
                    )
                    results.append(
                        {
                            "type": "text",
                            "text": (
                                _MAX_PREWRITE_LOCK_RESULT
                                if max_entry_write_required
                                else (
                                    _MAX_COORDINATOR_HANDOFF
                                    if coordinated_max
                                    else _MAX_DONE_WHEN_GREEN_NUDGE
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
            convo.append({"role": "user", "content": results})
            if (
                source_repair and not _repair_source_changed()
                and no_write_turns >= _SOURCE_REPAIR_NO_PROGRESS_TURNS
            ):
                return await _finish_without_provider(
                    steps=step + 1, reason="exploring", detail=_SOURCE_REPAIR_NUDGE,
                )
            if infra_dead_turns >= _INFRA_DEAD_ABORT_AT:
                log.warning("agent_native.infra_dead_abort", step=step)
                return AgentResult(
                    done=False,
                    summary="container/orchestrator unreachable — build aborted",
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="infra_error",
                    evidence=_evidence(),
                )
            if no_write_turns >= _NO_WRITE_ABORT_AT:
                if coordinated_max:
                    # Exploration exhaustion is also a provider stop. Reuse the
                    # source gate; only finalization may prove a ready product.
                    return await _finish_without_provider(
                        steps=step + 1,
                        reason="exploring",
                        detail="stuck without user-facing product progress",
                    )
                return AgentResult(
                    done=False,
                    summary=(
                        "stuck without user-facing product progress "
                        "(only reading/verifying or auxiliary-file churn)"
                    ),
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="exploring",
                    evidence=_evidence(),
                )

    # Hard stop means no more provider calls. A local build decides whether the
    # tree can ship; otherwise the caller restores the last green snapshot.
    return await _finish_without_provider(
        steps=effective_max_steps,
        reason="max_steps",
        detail="build failed",
    )


NativeSegmentRunner = Callable[
    [
        str,
        Callable[[Mapping[str, str], Mapping[str, int]], str | None] | None,
        Mapping[str, str],
    ],
    Awaitable[AgentResult],
]

_CONTINUATION_TERMINAL_REASONS = frozenset(
    {"error", "infra_error", "provider_error", "provider_stopped_red", "finalization_reserve",
     "output_limit"}
)


def _merge_segment_evidence(
    previous: Mapping[str, int],
    current: Mapping[str, int],
    *,
    wrote_files: bool,
) -> dict[str, int]:
    # A source write invalidates every earlier "*_after_write" proof. Native
    # segments expose proof relative to their own latest write, so only the new
    # segment may certify the resulting tree. A proof-only continuation can
    # safely add its evidence to the unchanged tree.
    if wrote_files:
        return dict(current)
    merged = dict(previous)
    for name, count in current.items():
        # Explicit zero is a proof tombstone, not an absent observation.
        merged[name] = count if name.endswith("_after_write") else max(merged.get(name, 0), count)
    return merged


def _cumulative_completion_check(
    completion_check: Callable[[Mapping[str, str], Mapping[str, int]], str | None] | None,
    baseline_files: Mapping[str, str],
    baseline_evidence: Mapping[str, int],
) -> Callable[[Mapping[str, str], Mapping[str, int]], str | None]:
    def check(
        written: Mapping[str, str],
        evidence: Mapping[str, int],
    ) -> str | None:
        if completion_check is None:
            return None
        effective_files = {**baseline_files, **written}
        effective_evidence = _merge_segment_evidence(
            baseline_evidence,
            evidence,
            wrote_files=bool(written),
        )
        return completion_check(effective_files, effective_evidence)

    return check


async def _run_native_segments(
    *,
    task: str,
    completion_check: Callable[[Mapping[str, str], Mapping[str, int]], str | None] | None,
    max_segments: int,
    run_segment: NativeSegmentRunner,
    initial_files: Mapping[str, str] | None = None,
) -> AgentResult:
    """Continue one native build without creating another run or workspace.

    Each provider segment gets a fresh transcript window, while the files and
    fact-gate evidence remain cumulative. Continuation is allowed only after
    observable file/proof progress; infrastructure failures and a whole
    no-progress segment stop immediately. ``max_segments`` is a runaway
    backstop, not a success condition.
    """

    segment_limit = max(1, int(max_segments))
    cumulative_files: dict[str, str] = {}
    cumulative_evidence: dict[str, int] = {}
    cumulative_transcript: list[dict[str, str]] = []
    total_steps = 0
    segment_task = task
    last_result: AgentResult | None = None

    for segment in range(1, segment_limit + 1):
        files_before = dict(cumulative_files)
        evidence_before = dict(cumulative_evidence)

        segment_completion_check = (
            _cumulative_completion_check(
                completion_check,
                {**(initial_files or {}), **files_before},
                evidence_before,
            )
            if completion_check is not None
            else None
        )

        result = await run_segment(
            segment_task, segment_completion_check, {**(initial_files or {}), **files_before}
        )
        last_result = result
        total_steps += result.steps
        cumulative_transcript.extend(result.transcript)
        wrote_files = bool(result.files)
        cumulative_files.update(result.files)
        cumulative_evidence = _merge_segment_evidence(
            cumulative_evidence,
            result.evidence,
            wrote_files=wrote_files,
        )

        combined = AgentResult(
            done=result.done,
            summary=result.summary,
            files=dict(cumulative_files),
            steps=total_steps,
            transcript=list(cumulative_transcript),
            stop_reason=result.stop_reason,
            evidence=dict(cumulative_evidence),
            segments=segment,
            needs_finalization=result.needs_finalization,
            proof_checkpoint=result.proof_checkpoint,
        )
        if result.done or result.needs_finalization or segment_limit == 1:
            return combined
        if result.stop_reason in _CONTINUATION_TERMINAL_REASONS:
            return combined

        file_progress = cumulative_files != files_before
        # Read/shell counters are observations, not new readiness proof. Repeated
        # successful exploration must not authorize another paid segment.
        evidence_progress = any(
            cumulative_evidence.get(f"{name}_after_write", 0) > 0
            and evidence_before.get(f"{name}_after_write", 0) <= 0
            for name in ("build", "runtime_check", "probe", "verify_isolation")
        )
        if not file_progress and not evidence_progress:
            combined.summary = (
                "Автономная генерация остановлена: целый сегмент не изменил "
                "продукт и не добавил проверяемых доказательств готовности."
            )
            combined.stop_reason = "no_progress"
            return combined
        if segment == segment_limit:
            combined.summary = (
                f"{result.summary}\n\nДостигнут защитный предел автономного продолжения "
                f"({segment_limit} сегм.)."
            ).strip()
            combined.stop_reason = "max_segments"
            return combined

        segment_task = (
            "Continue the autonomous build in the same GenerationRun and the same "
            "Project Cell. All files from the previous segment are preserved. Inspect "
            "the live tree, finish the remaining source/product gap, and call done "
            "when the implementation is complete. Deterministic final proof is reserved. "
            "Do not restart, scaffold over, or duplicate the project.\n\n"
            f"Original request:\n{task}\n\n"
            f"Previous segment stopped as {result.stop_reason}:\n{result.summary}"
        )
        # Give the outer cancellation watcher a deterministic boundary before
        # another provider request starts.
        await asyncio.sleep(0)

    assert last_result is not None  # segment_limit is always >= 1
    return last_result


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
    max_steps: int = 24,
    max_segments: int = 1,
    allow_max_bash: bool = False,
    portable_cell: bool = False,
    messages_url: str | None = None,
    messages_headers: Mapping[str, str] | None = None,
    messages_auth_factory: NativeMessagesAuthFactory | None = None,
    initial_files: Mapping[str, str] | None = None,
    edit_deadline: datetime | None = None,
    source_repair: bool = False,
    edit_source_changed: Callable[[], bool] | None = None,
) -> AgentResult:
    """Run one native generation, optionally continuing inside the same run."""

    async def run_segment(
        segment_task: str,
        segment_check: Callable[[Mapping[str, str], Mapping[str, int]], str | None] | None,
        initial_files: Mapping[str, str],
    ) -> AgentResult:
        return await _run_native_segment(
            system=system,
            task=segment_task,
            execute=execute,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            message_id=message_id,
            free=free,
            emit=emit,
            completion_check=segment_check,
            max_steps=max_steps,
            allow_max_bash=allow_max_bash,
            portable_cell=portable_cell,
            messages_url=messages_url,
            messages_headers=messages_headers,
            messages_auth_factory=messages_auth_factory,
            initial_files=initial_files,
            edit_deadline=edit_deadline,
            source_repair=source_repair,
            edit_source_changed=edit_source_changed,
        )

    return await _run_native_segments(
        task=task,
        completion_check=completion_check,
        max_segments=max_segments,
        run_segment=run_segment,
        initial_files=initial_files,
    )
