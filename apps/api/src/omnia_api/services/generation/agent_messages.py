from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_SECTION = re.compile(r"^\[(?P<name>[a-z][a-z0-9-]*)\]\s*$")
_TSC_DIAGNOSTIC = re.compile(r"^\S+\(\d+,\d+\):\s*error\s+TS\d+:")
_TAP_FAILURE = re.compile(r"^\s*not ok \d+\s*-\s*(?P<name>.+?)\s*$")
_STAGE_NAMES = {
    "typecheck": "проверка типов",
    "targeted-test": "тесты приложения",
    "install": "установка зависимостей",
    "build": "сборка",
}


def summarize_check_failure(detail: str) -> str:
    """Одна строка про то, ЧТО сломалось, а не первая строка отчёта.

    Отчёт проверки состоит из разделов: `[typecheck]`, `[targeted-test]` и так
    далее. Прежний код брал у него первую строку — а это всегда заголовок
    раздела. Владелец получал «осталась ошибка: [typecheck]», то есть ничего,
    и вдобавок неправду: в живом прогоне 23.09 проверка типов как раз прошла,
    а упали тесты приложения. Человек с такой подсказкой пойдёт искать ошибку
    типов, которой нет.

    Поэтому ищем первую строку, которая действительно про отказ, и называем
    раздел, в котором она нашлась. Если ничего распознать не удалось, ведём
    себя как раньше — первая непустая строка: неизвестность лучше выдумки.
    """
    section = ""
    fallback = ""
    for raw in detail.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        header = _SECTION.match(line.strip())
        if header is not None:
            section = header.group("name")
            continue
        if not fallback:
            fallback = line.strip()
        tap = _TAP_FAILURE.match(line)
        if tap is not None:
            return _prefixed(section, f"не прошёл тест «{tap.group('name')}»")
        if _TSC_DIAGNOSTIC.match(line.strip()):
            return _prefixed(section, line.strip())
    # Заголовки в запас не годятся: отчёт из одних заголовков не говорит ничего,
    # а целиком он в строку чата не поместится и только утопит смысл.
    return (fallback or "ошибка проверки")[:240]


def _prefixed(section: str, message: str) -> str:
    stage = _STAGE_NAMES.get(section, section.replace("-", " ") if section else "")
    return (f"{stage}: {message}" if stage else message)[:240]


def _failed_build_body(accumulated: str, stream_error: object) -> str:
    """Body to persist on the assistant message when a build stream errors.

    Keep any partial content the model managed to stream; otherwise write a
    human-readable error so a user who reloaded (and lost the ephemeral
    ``llm.error`` WS event) still sees WHY the build stopped instead of a
    blank, forever-"streaming" chat row. Mirrors ``_emergency_error``.
    """
    return accumulated if accumulated.strip() else f"[Ошибка генерации: {str(stream_error)[:300]}]"


def _capture_hard_coverage_attestation(
    capture: list[tuple[str, Any]] | None,
    verdict: Any,
    *,
    enabled: bool,
) -> list[tuple[str, Any]] | None:
    """Make a definitive hard coverage failure part of the durable release proof."""
    if verdict is None or not enabled or not verdict.hard_missing():
        return capture
    if capture is None:
        capture = []
    capture.append(("coverage", verdict))
    return capture


_STEP_VERB: dict[str, str] = {
    "write_file": "Пишу",
    "edit_file": "Правлю",
    "read_file": "Читаю",
    "list_dir": "Смотрю структуру",
    "grep": "Ищу в коде",
    "build": "Проверяю сборку",
    "bash": "Выполняю команду",
    "docs": "Читаю документацию",
    "provider_docs": "Читаю документацию провайдера",
    "runtime_check": "Открываю страницу",
    "probe": "Проверяю действие",
    "verify_isolation": "Проверяю безопасность данных",
    "generate_media": "Генерирую медиа",
    "done": "Готово",
}


_ROUTE_RU: dict[str, str] = {
    "dashboard": "панель",
    "chats": "чаты",
    "chat": "чат",
    "messages": "сообщения",
    "settings": "настройки",
    "profile": "профиль",
    "login": "вход",
    "signin": "вход",
    "signup": "регистрацию",
    "register": "регистрацию",
    "contacts": "контакты",
    "clients": "клиентов",
    "orders": "заказы",
    "products": "товары",
    "cart": "корзину",
    "checkout": "оформление",
    "admin": "админку",
    "users": "пользователей",
    "about": "«О нас»",
    "pricing": "цены",
    "blog": "блог",
    "new-group": "создание группы",
}


def _humanize_file(path: str) -> str:
    """A short Russian noun for a file path shown in a progress step."""
    p = (path or "").replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    if not p:
        return ""
    if base == "page.tsx" or base == "page.jsx":
        if p.rstrip("/").endswith("app/page.tsx") or "/app/page." in p:
            return "главную страницу"
        # Last meaningful segment = the route name (skip src/app + route groups).
        seg = [
            s
            for s in p.split("/")
            if s and not s.startswith("(") and s not in ("src", "app", "page.tsx", "page.jsx")
        ]
        if not seg:
            return "страницу"
        name = seg[-1]
        return f"страницу «{_ROUTE_RU.get(name, name)}»"
    if base in ("layout.tsx", "layout.jsx"):
        return "макет"
    if base == "globals.css" or base.endswith(".css"):
        return "стили"
    if base == "route.ts" or base == "route.js":
        return "API-обработчик"
    if "schema" in base:
        return "структуру данных"
    if base.endswith((".tsx", ".jsx")):
        return f"компонент {base.rsplit('.', 1)[0]}"
    return base


def _humanize_step(tool: str, path: str) -> str:
    """Turn a raw (tool, path) into a plain-language step, e.g.
    ('write_file', 'src/app/page.tsx') → 'Пишу главную страницу'."""
    tool = (tool or "").strip()
    verb = _STEP_VERB.get(tool)
    if verb is None:
        return tool or "Работаю"
    if tool in ("write_file", "edit_file"):
        what = _humanize_file(path)
        return f"{verb} {what}" if what else verb
    if tool == "read_file":
        what = _humanize_file(path)
        return f"{verb} {what}" if what else "Читаю код"
    if tool == "runtime_check" and path:
        return f"{verb} {path}"
    return verb


def _agent_product_failure(
    res: Any,
    *,
    verification_failed: bool,
    finalization_complete: bool,
) -> str | None:
    """A restored working baseline is not proof that the requested edit succeeded."""
    if finalization_complete:
        return None
    if verification_failed:
        return "final verification did not succeed"
    if not getattr(res, "done", False):
        reason = str(getattr(res, "stop_reason", ""))
        if not re.fullmatch(r"[a-z_]{1,64}", reason):
            reason = "incomplete"
        return f"agent did not finish the requested changes ({reason})"
    return None


def _agent_needs_continue_card(res: Any, *, product_failure: str | None) -> bool:
    """The «Продолжить» card belongs to an UNFINISHED run only.

    The run's verdict is ``_agent_product_failure``: a bounded exit that the
    finalization coordinator completed is a finished product — its status and its
    chat text say so, and a "Сборка не завершена" card next to «Готово» would
    contradict both.
    """
    return product_failure is not None and getattr(res, "stop_reason", "") == "max_steps"


def _agent_result_message(res: Any, *, is_edit: bool) -> str:
    """User-facing chat text for an agentic-build run — NEVER leaks the raw summary.

    ``res.summary`` is the model's own prose ONLY when it actually FINISHED (the
    model called ``done``). On every NON-done exit (ran out of steps / stalled /
    looping / gateway error) ``summary`` is an internal English diagnostic — e.g.
    "hit step budget without calling done" / "stuck repeating ..." — which must
    stay in the logs, NOT the chat (the bug: it was surfaced verbatim as Omnia's
    reply). Partial files still commit, so on a non-finish we show a friendly RU
    message that keeps the user moving («Починить» / повтори), keyed to why the
    agent stopped."""
    if getattr(res, "done", False) or getattr(res, "needs_finalization", False):
        return (getattr(res, "summary", "") or "").strip() or (
            "Готово — правка применена." if is_edit else "Готово — приложение собрано."
        )
    if getattr(res, "stop_reason", "") == "max_steps":
        # Status prose only — a «Продолжить» card is published alongside (see the
        # agent path) and carries the resume action, so don't repeat it here.
        return (
            "Не успел доделать правку за отведённые шаги — часть уже на месте."
            if is_edit
            else "Собрал приложение не полностью за отведённые шаги — часть уже на месте."
        )
    if getattr(res, "stop_reason", "") in {
        "max_steps_rolled_back",
        "provider_stopped_rolled_back",
        "unsafe_changes_rolled_back",
        "safe_starter_rolled_back",
        "core_only_rolled_back",
        "core_preparation_failed",
        "verification_rolled_back",
    }:
        return (getattr(res, "summary", "") or "").strip() or "Сборка не завершена."
    return (
        "Не удалось завершить правку" if is_edit else "Сборка прервана"
    ) + ". Попробуй ещё раз или нажми «Починить»."


_MAX_STABLE_SINGLE_PASS_STEPS = 40


def _agent_step_budget(project_template: str, *, configured_steps: int) -> int:
    """Return the turn budget for each coherent provider segment.

    The verified early MAX production loop (revision ``217328d9``) completed a
    real five-screen app in 40 steps, so MAX keeps that floor per segment. The
    same-run continuation layer may open another fresh transcript only when the
    prior segment made measurable file/proof progress and the completion contract
    is still open; a no-progress segment stops instead of repeatedly expanding a
    working product. Other stacks retain their configured budget and one segment.
    """

    steps = max(1, int(configured_steps))
    if project_template == "max_miniapp":
        return max(steps, _MAX_STABLE_SINGLE_PASS_STEPS)
    return steps


_CONTINUE_KEYWORDS: frozenset[str] = frozenset(
    {
        "продолж",  # продолжи / продолжай / продолжить
        "доделай",
        "доделать",
        "доведи",
        "довести",
        "дособери",
        "дособерите",
        "достро",  # дострой/достроить
        "доработай",
        "допиши",
        "заверши сбор",
        "закончи сбор",
        "не доделал",
        "не докончил",
        "до конца",
        "finish the build",
        "continue building",
        "keep building",
    }
)


def _is_continue_request(prompt: str) -> bool:
    """True when a follow-up asks to FINISH the partial agentic build (see above).

    Caller gates this on a non-first-build CONTAINER project so it only fires when
    there is actually a partial app to continue."""
    t = (prompt or "").strip().lower()
    return any(k in t for k in _CONTINUE_KEYWORDS)


def _recover_max_resume_prompt(candidates: Sequence[str]) -> str | None:
    """Return the latest real brief behind one or more failed MAX resumes.

    A service/config snapshot can exist before the first product snapshot. In
    that state the UI's ``продолжи`` is not a new brief and must not replace the
    original request sent to the Google agent. Candidates are newest-first.
    """
    for candidate in candidates:
        value = (candidate or "").strip()
        if value and not _is_continue_request(value):
            return value
    return None
