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
import re
from collections.abc import Awaitable, Callable, Mapping
from itertools import count
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
_CALL_RETRIES = 1  # never duplicate a possibly-billed provider request inside one cycle
_MAX_PROVIDER_RECONNECT_CYCLES = 3

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

# MAX Studio's proven pre-Gemini loop had no planning/capability/skill protocol:
# the model inspected files, wrote the product, built, fixed, and called done.
# Keep the current safe executor surface, but hide later orchestration ceremony.
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
        "see",
        "generate_media",
        "probe",
        "verify_isolation",
        "done",
    }
)
_STABLE_MAX_TOOLS = [tool for tool in _TOOLS if tool["name"] in _STABLE_MAX_TOOL_NAMES]
_STABLE_MAX_TOOLS_CACHED: list[dict[str, Any]] = [
    *_STABLE_MAX_TOOLS[:-1],
    {**_STABLE_MAX_TOOLS[-1], "cache_control": _CACHE},
]

# MAX has a narrower executor contract. Do not advertise operations which the
# server will always reject or which authenticate through the generic web
# harness (probe/isolation). Gemini otherwise spends turns discovering the
# rejection even though the MAX prompt already says not to call them.
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
_MAX_TOOLS_CACHED: list[dict[str, Any]] = [
    *_MAX_TOOLS[:-1],
    {**_MAX_TOOLS[-1], "cache_control": _CACHE},
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
    "и располагай внешние font-import ДО него. Не трогай locked layout/provider/runtime. "
    "Определи собственные семантические CSS variables (`--app-*`) и используй их через "
    "обычный CSS или Tailwind arbitrary values; не вызывай несуществующие "
    "`bg-background`/`border-border` без явного mapping. Один доминирующий акцент, "
    "выразительная типографическая шкала, осмысленные поверхности и ритм важнее радуги, "
    "градиентов и множества одинаковых карточек.\n\n"
    "МОБИЛЬНЫЙ ПРОДУКТ, НЕ САЙТ. Проектируй сначала для 360–390px: главное действие "
    "видно сразу, навигация не перекрывает контент, safe-area учтён, tap targets удобны "
    "для пальца, данные читаются без горизонтального скролла. Используй реальный профиль "
    "MAX и Bridge там, где это улучшает сценарий. Loading, empty, error/retry, success, "
    "selected/pressed/disabled — полноценные состояния, а не подписи в макете.\n\n"
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
    "[LOOP GUARD] The MAX build is clean. Run runtime_check once after the final write "
    "and run see through the signed MAX preview. If see returns a concrete visual issue, "
    "apply it, rebuild, and see the repaired product again; otherwise call done NOW. Do "
    "not call generic probe or verify_isolation."
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
    "Ты — автономный Google AI-агент, который за один непрерывный проход строит "
    "полноценный MAX Mini App. Работай прямо с проектом: коротко изучи защищённое "
    "ядро, затем сразу напиши весь продукт по брифу — экраны, навигацию, состояния, "
    "взаимодействия и аккуратный mobile-first дизайн. Не используй визуальный шаблон, "
    "не подменяй функции текстом, TODO или декоративными кнопками. Не останавливайся "
    "на частичном результате и не объявляй успех словами.\n\n"
    "Сохрани управляемую MAX-обвязку: подписанный initData, useMaxApp/профиль, "
    "integration-client, webhook и закрытые Studio-файлы. Пользовательские данные "
    "бери из MAX и управляемого серверного хранилища; не зашивай демо-профили, "
    "историю, метрики или секреты. Не создавай параллельную email-регистрацию.\n\n"
    "Надёжный цикл: минимально прочитай нужные файлы → пиши полные продуктовые файлы "
    "→ build → исправь каждую реальную ошибку → runtime_check корневого экрана после "
    "последней записи. Если runtime_check красный, прочитай конкретный файл/лог и "
    "перезапиши его полностью вместо серии хрупких edit_file. Вызови done только после "
    "чистого build и зелёного runtime_check. see можно использовать один раз для "
    "точечной визуальной проверки, но недоступность visual QA не блокирует рабочий "
    "продукт. Не трать ходы на церемониальный план, skill-пакеты или внешнее исследование, "
    "если без него можно сразу собрать приложение."
)

_MAX_REFERENCE_EDIT_PREAMBLE = (
    "Ты — автономный Google AI-агент для точечной правки существующего MAX Mini App. "
    "Сначала прочитай только целевой участок, затем внеси минимальное изменение, "
    "сохрани все остальные экраны, данные и сценарии. Не переписывай весь продукт, "
    "не меняй визуальное направление без прямого запроса и не трогай управляемое "
    "MAX-ядро. После последней записи исправь фактические ошибки build/runtime_check "
    "и заверши только на зелёной версии. Не создавай демо-данные, секреты, параллельную "
    "email-авторизацию, API или прямой доступ к БД."
)


def native_system_prompt(
    stack_guide: str,
    skills: str | None = None,
    *,
    reference_max_loop: bool = False,
    reference_max_edit: bool = False,
) -> str:
    """Native-tools system prompt: a short tool-loop preamble + the stack guide (+
    skills). Deliberately DROPS the text-``<omnia:action>`` LOOP_PROTOCOL — the tool
    schemas ARE the protocol now, so keeping it would only confuse a native model."""
    guide = (stack_guide or "").strip()
    # MAX used the generic native Anthropic loop before the Gemini migration.
    # Keep the compatibility kwargs so callers and old transcripts remain valid,
    # but do not inject Gemini-only lifecycle/skill/reference protocols.
    _ = reference_max_loop, reference_max_edit
    parts = [_NATIVE_PREAMBLE, guide]
    if skills and skills.strip():
        parts.append(skills.strip())
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
    model: str = _MODEL,
) -> dict[str, Any]:
    """One native /v1/messages call with 429 (concurrency) retry. Returns the parsed
    Anthropic response dict, or raises the last error."""
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "thinking": {"type": "enabled", "budget_tokens": _THINKING_BUDGET},
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
        }
        try:
            r = await client.post(url, json=payload, timeout=_HTTP_TIMEOUT_S)
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
    wrote_since_build = False
    no_write_turns = 0  # consecutive assistant turns with no successful write
    infra_dead_turns = 0  # consecutive turns where EVERY tool op died on infra
    successful_tools: dict[str, int] = {}
    successful_skill_ids: set[str] = set()
    proof_after_write: set[str] = set()
    visual_feedback_step: int | None = None
    last_green_see_step: int | None = None
    pending_visual_evaluation_step: int | None = None
    visual_evaluation_ready = False
    provider_reconnect_cycles = 0

    max_runtime = "MAX VERIFICATION OVERRIDE" in system or reference_max_loop
    max_lifecycle = max_runtime and completion_check is not None and enforce_max_skill_lifecycle
    unbounded_max_runtime = max_runtime
    effective_max_steps = (
        None if unbounded_max_runtime else max(1, int(40 if max_steps is None else max_steps))
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
        step_numbers = count() if effective_max_steps is None else range(effective_max_steps)
        for step in step_numbers:
            if pending_visual_evaluation_step is not None and step > pending_visual_evaluation_step:
                visual_evaluation_ready = True
            call_stage = (
                "build_plan"
                if step == 0
                else "verification"
                if last_build_ok is True and not wrote_since_build
                else "native_agent"
            )
            try:
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
                    tools=(
                        _STABLE_MAX_TOOLS_CACHED
                        if stable_max_loop
                        else _MAX_REFERENCE_TOOLS_CACHED
                        if reference_max_loop
                        else _MAX_TOOLS_CACHED
                        if max_runtime
                        else _TOOLS_CACHED
                    ),
                    model=model,
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
                                "AI-провайдер отклонил запрос; повтор не поможет. "
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
            ops_this_turn = 0  # executed (non-done) tool ops this turn
            infra_this_turn = 0  # of those, how many died on infra
            for tu in tool_uses:
                name = tu.get("name", "")
                tu_id = tu.get("id", "")
                if name == "done":
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
                if lifecycle_error:
                    obs = {"ok": False, "error": lifecycle_error}
                else:
                    try:
                        obs = await execute(action)
                    except Exception as exc:  # a tool crash must not kill the build
                        obs = {"ok": False, "error": f"tool {name} crashed: {exc}"}
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
                    if name == "write_file":
                        written[action.path] = action.args.get("content", "")
                    elif isinstance(obs.get("content"), str):
                        # executor returns the post-edit content (mirrors the
                        # text loop's tracking at agent_builder.py) — closes the
                        # gap where edit_file never dirtied the done fact-gate.
                        written[action.path] = obs["content"]
                    wrote_since_build = True
                    wrote_this_turn = True
                    # Tool calls in one assistant response are planned before
                    # any result is returned. Only a write from a LATER turn can
                    # have applied the visual critique.
                    if visual_feedback_step is not None and step > visual_feedback_step:
                        visual_feedback_step = None
                    proof_after_write.clear()
                    last_green_see_step = None
                elif name == "build":
                    last_build_ok = bool(obs.get("ok"))
                    wrote_since_build = False
                if obs.get("ok"):
                    successful_tools[name] = successful_tools.get(name, 0) + 1
                    if name == "read_skill":
                        skill_id = str(action.args.get("skill") or "").strip().casefold()
                        if skill_id:
                            successful_skill_ids.add(skill_id)
                        if skill_id == MAX_REQUIRED_POST_SEE_SKILL and max_lifecycle:
                            pending_visual_evaluation_step = step
                    if name in {"build", "runtime_check", "see", "probe", "verify_isolation"}:
                        if name == "see" and obs.get("needs_fix"):
                            visual_feedback_step = step
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
            else:
                no_write_turns += 1
                if _NO_WRITE_NUDGE_AT <= no_write_turns and (
                    unbounded_max_runtime or no_write_turns < _NO_WRITE_ABORT_AT
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
            convo.append({"role": "user", "content": results})
            if infra_dead_turns >= _INFRA_DEAD_ABORT_AT and not unbounded_max_runtime:
                log.warning("agent_native.infra_dead_abort", step=step)
                return AgentResult(
                    done=False,
                    summary="container/orchestrator unreachable — build aborted",
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="infra_error",
                )
            if infra_dead_turns >= _INFRA_DEAD_ABORT_AT and unbounded_max_runtime:
                log.warning("agent_native.max_infra_reconnecting", step=step)
                await asyncio.sleep(15.0)
            if no_write_turns >= _NO_WRITE_ABORT_AT and not unbounded_max_runtime:
                return AgentResult(
                    done=False,
                    summary="stuck exploring (reading/verifying) without writing any file",
                    files=written,
                    steps=step + 1,
                    transcript=convo,
                    stop_reason="exploring",
                )

    # Only bounded generic builds can exhaust this iterator. MAX uses count(),
    # while explicit green/budget/provider/cancellation branches return above.
    assert effective_max_steps is not None
    return await _finish_without_provider(
        steps=effective_max_steps,
        reason="max_steps",
        detail="build failed",
    )
