"""Native Anthropic tool-use build loop — the Claude-Code-grade agent (DARK).

Supersedes the text-``<omnia:action>`` protocol (``agent_builder.run_agent_build``)
with **native Anthropic tool-use**: ONE strong model (opus-4-8) drives the whole build
end-to-end via real tool calls, with extended thinking PRESERVED across tool turns
(thinking blocks are echoed back verbatim — Anthropic 400s otherwise, and stripping
them is exactly what derailed the text loop). The only "gate" is FACT-based: the
``build`` tool returns the real compiler errors as a ``tool_result`` and the model
fixes them itself (do → check → fix), like Claude Code — no taste/vision judges here.

Owns ONLY the loop + protocol. Reuses ``agent_builder.make_container_executor`` for
the actual file/container ops, and calls the gateway's native ``/v1/messages``
passthrough (``routers/messages_native.py``) so the thinking-block signatures survive
the round-trip.

Behind ``settings.use_native_agent`` (default OFF): the existing ``run_agent_build``
stays the prod default until this is verified on real builds and billing is wired.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from omnia_api.core.config import get_settings
from omnia_api.services.agent_builder import Action, AgentResult

log = structlog.get_logger(__name__)

_MODEL = "claude-opus-4-8"
# AITunnel PRE-RESERVES the full max_tokens × output price on every call and 402s
# if the key balance is below that reserve — so an over-large ceiling caps how many
# calls fit the balance (32000 reserved ~₽173/call → a low balance 402s mid-build,
# which surfaced to the user as "соединение потеряно"). 20000 still leaves ~12000
# tokens for tool args after the 8000 thinking budget — enough for a large file —
# while cutting the reserve ~35%. Env override: NATIVE_MAX_TOKENS (future).
_MAX_TOKENS = 20000
_THINKING_BUDGET = 8000
_MAX_TOOL_RESULT_CHARS = 20000
_HTTP_TIMEOUT_S = 300.0
_CALL_RETRIES = 8  # oneprovider: 429 concurrency cap + sustained 502/504 flake bursts

# EXPLORE-STALL guard — parity with run_agent_build's no_write_streak
# (agent_builder._NO_WRITE_NUDGE_AT/_NO_WRITE_ABORT_AT = 5/14, which count single
# text-protocol ACTIONS). Native counts assistant TURNS instead — one turn often
# bundles several parallel tool calls, and the native flow legitimately spends
# its first turns surveying the big template — so the nudge fires later (6 turns
# ≈ 8-15 read calls) and the abort at 12 turns still saves 28 slow Opus steps.
_NO_WRITE_NUDGE_AT = 6
_NO_WRITE_ABORT_AT = 12

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
    _tool("grep", "Regex-search files under a path.",
          {"pattern": _STR, "path": _STR}, ["pattern"]),
    _tool("docs", "Fetch up-to-date external-library docs (Context7) so you use the "
          "REAL current API, not a stale/guessed one.",
          {"library": _STR, "query": _STR}, ["library", "query"]),
    _tool("write_file", "Create or overwrite a whole file with its FULL content.",
          {"path": _STR, "content": _STR}, ["path", "content"]),
    _tool("edit_file", "Replace an exact, unique snippet inside a file.",
          {"path": _STR, "search": _STR, "replace": _STR},
          ["path", "search", "replace"]),
    _tool("build", "Typecheck/compile the app. Returns the real errors to fix "
          "(empty = clean).", {}),
    _tool("bash", "Run a shell command in the dev container.", {"cmd": _STR}, ["cmd"]),
    _tool("read_logs", "Tail the live dev-server logs (runtime errors build can't see).",
          {"tail": {"type": "integer"}}),
    _tool("runtime_check", "Open a route in the RUNNING app and get the REAL HTTP "
          "status — a typecheck-clean app can still 5xx on render.",
          {"path": _STR}, ["path"]),
    _tool("see", "LOOK at a rendered route with your eyes: screenshots the running "
          "page (desktop + mobile) and returns a strict vision-designer critique — "
          "concrete fixes (hero too small, 3 identical cards, weak contrast, cramped "
          "spacing, generic look). A clean build does NOT mean it looks good; `see` "
          "is the only way to judge and fix TASTE. Default path '/'.",
          {"path": _STR}),
    _tool("generate_media", "GENERATE a real IMAGE or short VIDEO with AI (same key) "
          "and get back a hosted URL to EMBED (returned in the tool result — copy it "
          "into src). kind='image' (flux, ~5s — photoreal hero/section still). "
          "kind='video' (Kling, ~1-3 min) — the SIGNATURE move is KEYFRAME "
          "INTERPOLATION: pass first_frame AND last_frame (each a vivid English scene "
          "prompt) and Flux paints both stills while Kling generates the UNIQUE motion "
          "BETWEEN them — a real fly-through ('летишь по острову при скролле'), not a "
          "generic loop. `prompt` = the MOTION/camera between the two frames. Each "
          "stage shows as its own live step. Optional: duration (3-10s), aspect "
          "('16:9'|'9:16'|'1:1'); first_frame_url/last_frame_url to reuse an already-"
          "made still instead of a prompt. Embed `<img src>` / `<video src autoPlay "
          "muted loop playsInline>` (or scroll-scrub currentTime). Video is SLOW + "
          "pricey (hard cap per build) — ONE key clip, reuse it, do NOT spam per-card.",
          {"kind": _STR, "prompt": _STR, "first_frame": _STR, "last_frame": _STR,
           "duration": {"type": "integer"}, "aspect": _STR,
           "first_frame_url": _STR, "last_frame_url": _STR, "image_url": _STR},
          ["kind", "prompt"]),
    _tool("probe", "Make a REAL request AS A LOGGED-IN test user and get the exact "
          "status+body — the only way to prove an interactive feature (create/"
          "save/submit) works end-to-end, which a clean build + 200 page do NOT.",
          {"method": _STR, "path": _STR, "body": {"type": "object"}}, ["path"]),
    _tool("verify_isolation", "PROVE no cross-tenant leak: logs in TWO users, A "
          "creates the resource, then asserts B is DENIED reading it AND it is "
          "absent from B's list. Run for EVERY owned resource — a green build "
          "never proves isolation.",
          {"create": {"type": "object"}, "read": {"type": "object"}}, ["create"]),
    _tool("done", "Finish — the requested app is built AND the last build is clean. "
          "`summary` = structured RU markdown for the user (bold one-line result, then "
          "«## » sections by meaning, `code` for identifiers, lists) — see the preamble.",
          {"summary": _STR}, ["summary"]),
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


def _obs_to_tool_result(tool_use_id: str, obs: dict[str, Any]) -> dict[str, Any]:
    ok = bool(obs.get("ok"))
    body = obs.get("content") or obs.get("detail") or obs.get("error") or (
        "ok" if ok else "error"
    )
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
        h for h in (
            _module_not_found_hint(build_output),
            _parallel_pages_hint(build_output),
        ) if h
    )


def _text_of(content: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(b.get("text", "")) for b in content
        if isinstance(b, dict) and b.get("type") == "text"
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
    if name in ("grep", "list_dir", "bash", "read_logs", "docs"):
        return _cap(obs.get("detail") or obs.get("content") or "")
    if name in ("runtime_check", "probe", "verify_isolation"):
        return _cap(obs.get("detail") or obs.get("content") or "проверка пройдена")
    return _cap(obs.get("detail") or obs.get("content") or "")


_NATIVE_PREAMBLE = (
    "Ты — автономный инженер: строишь РАБОЧЕЕ приложение в этом проекте, как Claude "
    "Code. Инструменты вызывай напрямую: read_file/list_dir/grep — понять код, "
    "write_file/edit_file — писать, build — компиляция, bash/read_logs — рантайм, "
    "runtime_check — открыть роут в ЖИВОМ приложении, probe — реальный запрос ОТ "
    "ИМЕНИ залогиненного юзера, verify_isolation — доказать отсутствие утечки данных "
    "между юзерами, docs — свежая дока библиотек. Думай сколько нужно. Цикл: пиши "
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
    "2 ключевых кадра (старт сцены + финал) ⇒ Kling соединяет их в УНИКАЛЬНЫЙ пролёт "
    "(интерполяция) ⇒ фронт крепит скролл-скрабом (`currentTime` от прогресса) — "
    "картинка «летит» при прокрутке. Один `generate_media(kind='video', first_frame, "
    "last_frame, prompt=движение)` запускает всю цепочку.\n"
    "• «фотореалистичный герой/секция, атмосфера» → Flux-картинка (kind='image').\n"
    "• обычный контентный сайт → без видео, но ВСЕГДА живые hover/reveal (см. ниже).\n"
    "Модели ВЗАИМОДЕЙСТВУЮТ так: Flux (кадры-стоп-кадры) → Kling (движение МЕЖДУ "
    "кадрами) → фронт (скролл/hover-оживление). В финальном ответе (done) КОРОТКО "
    "объясни пользователю, какая связка сработала и почему — чтобы он видел замысел.\n\n"
    "МЕДИА (картинки + КИНО-ВИДЕО) — у тебя есть `generate_media`, тот же ключ, "
    "возвращает готовый URL (он приходит в результате тула — ВСТАВЬ его в src). "
    "kind='image' — фото-герой/секции (flux). kind='video' — коронный приём "
    "КЕЙФРЕЙМ-ИНТЕРПОЛЯЦИЯ: передай first_frame И last_frame (промпт каждой сцены) — "
    "Flux нарисует ОБА кадра (первый и последний), а Kling сделает УНИКАЛЬНЫЙ плавный "
    "переход-пролёт между ними (`prompt` = движение/камера между кадрами). Это и есть "
    "«летишь по острову при скролле»: first_frame='аэросъёмка края острова, рассвет', "
    "last_frame='камера у вулкана крупным планом, золотой свет', prompt='плавный "
    "облёт вперёд над джунглями'. Каждый этап (первый кадр → последний кадр → склейка "
    "Kling) виден пользователю отдельным шагом. Встраивание: (a) фоновый луп — "
    "`<video autoPlay muted loop playsInline>` в `absolute inset-0 object-cover -z-10`, "
    "контент поверх; (b) скролл-скраб «летишь при скролле» — sticky-контейнер на "
    "100–300vh, `video.currentTime` привязан к прогрессу скролла (обнови в "
    "requestAnimationFrame на onScroll, `preload=auto` `muted`). Всегда `poster=` + "
    "градиент-оверлей для читаемости текста. Видео МЕДЛЕННОЕ и дорогое (жёсткий лимит "
    "клипов на сборку) — 1 ключевой клип, переиспользуй, НЕ по клипу на карточку. Не "
    "медиа ради медиа — только когда усиливает смысл ниши.\n\n"
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
    "• Первая строка — ИТОГ одним предложением, ключевой результат выдели **жирным**. Без заголовка над ней.\n"
    "• Дальше — секции с заголовками «## » ПО СМЫСЛУ (бери только нужные, обычно 2–4): "
    "что сделал · как это работает · что проверил · что дальше.\n"
    "• `бэктики` — на КАЖДЫЙ идентификатор: имена файлов, функций, флагов, роутов, команд, полей.\n"
    "• **жирным** — ключевые фичи и сущности; *курсивом* — нюанс, оговорку, «почему так».\n"
    "• Списки «- » — для перечислений (что изменилось, шаги, проверки).\n"
    "• Простыми словами, без канцелярита и без «я выполнил задачу»; технический термин — "
    "с коротким пояснением в скобках. По делу и развёрнуто (что сделал → зачем → эффект), без воды."
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


def native_system_prompt(stack_guide: str, skills: str | None = None) -> str:
    """Native-tools system prompt: a short tool-loop preamble + the stack guide (+
    skills). Deliberately DROPS the text-``<omnia:action>`` LOOP_PROTOCOL — the tool
    schemas ARE the protocol now, so keeping it would only confuse a native model."""
    parts = [_NATIVE_PREAMBLE, (stack_guide or "").strip()]
    if skills and skills.strip():
        parts.append(skills.strip())
    return "\n\n".join(p for p in parts if p)


async def _call_messages(
    client: httpx.AsyncClient, url: str, convo: list[dict[str, Any]], system: str
) -> dict[str, Any]:
    """One native /v1/messages call with 429 (concurrency) retry. Returns the parsed
    Anthropic response dict, or raises the last error."""
    import asyncio

    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "thinking": {"type": "enabled", "budget_tokens": _THINKING_BUDGET},
        # Prompt caching: cache the stable system prompt + tool schemas, and a
        # moving breakpoint on the transcript tail (see _with_incremental_cache).
        "system": _system_blocks(system),
        "tools": _TOOLS_CACHED,
        "tool_choice": {"type": "auto"},
        "messages": _with_incremental_cache(convo),
    }
    last: Exception | None = None
    for attempt in range(_CALL_RETRIES):
        try:
            r = await client.post(url, json=payload, timeout=_HTTP_TIMEOUT_S)
            # 402 = provider key out of balance. Retrying can't fix it, so fail
            # FAST with a human cause instead of grinding 8 backoff retries and
            # surfacing an opaque "соединение потеряно" 3+ minutes later.
            if r.status_code == 402:
                raise RuntimeError(
                    "PAYMENT_REQUIRED: баланс LLM-провайдера (AITunnel) исчерпан — "
                    "пополни ключ и повтори промпт"
                )
            if r.status_code == 429 or (
                r.status_code >= 400 and "rate_limit" in r.text[:300]
            ):
                await asyncio.sleep(6.0 * (attempt + 1))
                last = RuntimeError(f"429 concurrency (attempt {attempt + 1})")
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            # oneprovider flakes in SUSTAINED bursts (observed live: series of
            # 502s + 504s over several minutes killed builds mid-run). Linear
            # 3-15s backoff only covered ~30s; exponential-with-cap rides out a
            # multi-minute flake window (~3.5 min total) before giving up.
            last = exc
            await asyncio.sleep(min(45.0, 4.0 * (2 ** attempt)))
    raise last or RuntimeError("messages call failed")


async def run_native_build(
    *,
    system: str,
    task: str,
    execute: Callable[[Action], Awaitable[dict[str, Any]]],
    user_id: Any = None,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    max_steps: int = 40,
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
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/messages"

    convo: list[dict[str, Any]] = [{"role": "user", "content": task}]
    written: dict[str, str] = {}
    last_build_ok: bool | None = None
    wrote_since_build = False
    done_rejections = 0
    # Room to bounce a premature done and actually heal (a hallucinated-module
    # build usually needs a couple of correction turns) before we let it ship red.
    _DONE_REJECT_CAP = 5
    no_write_turns = 0  # consecutive assistant turns with no successful write
    infra_dead_turns = 0  # consecutive turns where EVERY tool op died on infra

    async with httpx.AsyncClient() as client:
        for step in range(max_steps):
            try:
                resp = await _call_messages(client, url, convo, system)
            except Exception as exc:
                return AgentResult(
                    done=False, summary=f"gateway error: {exc}",
                    files=written, steps=step, transcript=convo, stop_reason="error",
                )

            content = resp.get("content")
            if not isinstance(content, list):
                return AgentResult(
                    done=False, summary="malformed upstream (no content list)",
                    files=written, steps=step + 1, transcript=convo, stop_reason="error",
                )
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

            tool_uses = [
                b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            if not tool_uses:
                # Model ended its turn with prose and no tool — it's done talking.
                # A prose-less finish must NOT leak "(no tool call)" to the chat
                # (observed live: it became the user-visible assistant message on
                # a template-already-covers-it build). Human fallback for done;
                # the technical marker stays for the non-done diagnostics path.
                _done = resp.get("stop_reason") == "end_turn"
                _text = _text_of(content)
                if not _text:
                    _text = (
                        "Готово — приложение собрано и проверено. Открой превью."
                        if _done
                        else "(no tool call)"
                    )
                return AgentResult(
                    done=_done,
                    summary=_text,
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="no_tool",
                )

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
                    # never confirmed a CLEAN build afterwards. Bounded (R-10).
                    premature = wrote_since_build or last_build_ok is not True
                    if premature and done_rejections < _DONE_REJECT_CAP:
                        done_rejections += 1
                        results.append({
                            "type": "tool_result", "tool_use_id": tu_id, "is_error": True,
                            "content": "Not done yet: run the `build` tool and make it "
                                       "CLEAN (fix any errors) before calling done.",
                        })
                        continue
                    done_summary = str((tu.get("input") or {}).get("summary", ""))
                    results.append({"type": "tool_result", "tool_use_id": tu_id, "content": "done"})
                    continue

                action = _tool_use_to_action(tu)
                try:
                    obs = await execute(action)
                except Exception as exc:  # a tool crash must not kill the build
                    obs = {"ok": False, "error": f"tool {name} crashed: {exc}"}
                # Emit AFTER execute so the step carries a `detail` — what the tool
                # actually did (written content preview, build output, read result)
                # — so the UI can let the user drill INTO a step and see inside it.
                if emit:
                    await emit("agent.step", {
                        "step": step, "action": name, "path": action.path,
                        "detail": _step_detail(name, action, obs),
                        "ok": bool(obs.get("ok", True)),
                    })

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
                elif name == "build":
                    last_build_ok = bool(obs.get("ok"))
                    wrote_since_build = False
                _tr = _obs_to_tool_result(tu_id, obs)
                if name == "build" and not obs.get("ok"):
                    _hint = _build_error_hint(str(_tr.get("content") or ""))
                    if _hint:
                        _tr["content"] = str(_tr["content"]) + _hint
                results.append(_tr)

            if done_summary is not None:
                if emit:
                    await emit("agent.done", {"step": step, "files": len(written)})
                return AgentResult(
                    done=True, summary=done_summary, files=written,
                    steps=step + 1, transcript=convo, stop_reason="done",
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
            if wrote_this_turn:
                no_write_turns = 0
            else:
                no_write_turns += 1
                if _NO_WRITE_NUDGE_AT <= no_write_turns < _NO_WRITE_ABORT_AT:
                    results.append({
                        "type": "text",
                        "text": (
                            _DONE_WHEN_GREEN_NUDGE
                            if last_build_ok is True and not wrote_since_build
                            else _EXPLORE_STALL_NUDGE
                        ),
                    })
                    if emit:
                        await emit("agent.stalled", {"step": step})
            convo.append({"role": "user", "content": results})
            if infra_dead_turns >= _INFRA_DEAD_ABORT_AT:
                log.warning("agent_native.infra_dead_abort", step=step)
                return AgentResult(
                    done=False,
                    summary="container/orchestrator unreachable — build aborted",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="error",
                )
            if no_write_turns >= _NO_WRITE_ABORT_AT:
                return AgentResult(
                    done=False,
                    summary="stuck exploring (reading/verifying) without writing any file",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="exploring",
                )

    return AgentResult(
        done=False, summary="hit step budget without calling done",
        files=written, steps=max_steps, transcript=convo, stop_reason="max_steps",
    )
