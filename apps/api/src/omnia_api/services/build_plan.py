"""Build Plan — pre-build feature spec (owner directive 2026-06-30 «эскиз перед
стройкой, не останавливайся на зелёном минимуме»).

Before the agent writes a line of code, a *planner* pass (Opus) turns the user's
prompt into a STRUCTURED, BOUNDED feature plan: the screens the app must have,
the data entities behind them, and the concrete user *capabilities* — each an
authorized request that must return 2xx. The plan is two things at once:

* **the build checklist** — injected into the build prompt so the agent builds
  the WHOLE plan instead of a thin, green-compiling subset, and
* **the completion contract** — what :mod:`services.coverage_gate` checks so that
  "done" means "the plan is covered", not "the build is green".

It is persisted inside ``projects.discovery_spec`` (JSONB) under the
``build_plan`` key, so a follow-up prompt / edit reads it back via
:func:`read_plan` and is held to the same plan.

**Fail-soft (R-10).** A planner gateway/parse error, mock mode, or the
``use_build_plan`` flag being off all yield an EMPTY :class:`BuildPlan`, and an
empty plan means EXACTLY today's behaviour: no checklist injected, no coverage
gate run. This feature can never make a build worse than it is today.

The single public surface mirrors the project's other services: a trivial
``await plan_build(...) -> BuildPlan`` hides the prompt + JSON contract; pure
helpers (``read_plan`` / ``to_dict`` / ``from_dict`` / ``checklist_block``) carry
no I/O so they unit-test without a gateway.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from omnia_api.core.config import get_settings, model_for_role
from omnia_api.services.llm_client import LLMError, complete_chat

log = logging.getLogger(__name__)

# Hard caps — a plan must stay CHECKABLE, so it can never explode. The planner is
# told these limits in the system prompt AND we truncate defensively here, so a
# model that ignores the limit still cannot blow up the coverage loop.
_MAX_SCREENS = 8
_MAX_ENTITIES = 8
_MAX_CAPABILITIES = 10

_VALID_ROLES = ("guest", "user", "admin")


def _clean_str(v: Any, *, limit: int = 200) -> str:
    return str(v).strip()[:limit] if v is not None else ""


@dataclass(frozen=True)
class Capability:
    """One user action that must WORK — the coverage currency.

    ``method`` + ``path`` describe the authorized request the coverage gate
    replays (``coverage_gate`` → ``agent_probe.run_probe``); ``expect`` is the
    status class it must return (``"2xx"`` for the happy path, ``"403"`` /
    ``"404"`` for an isolation / negative check). A ``must_have`` capability with
    a concrete ``path`` BLOCKS completion until it answers as expected; a
    capability with no ``path`` is UI-only (not probeable) → advisory, never
    blocks (keeps the hard gate honest — we only block on what we can prove).
    """

    id: str
    actor_role: str = "user"
    action: str = ""
    method: str = "POST"
    path: str = ""
    body_hint: dict[str, Any] = field(default_factory=dict)
    expect: str = "2xx"
    must_have: bool = True

    @property
    def probeable(self) -> bool:
        """A capability the coverage gate can actually replay (has an HTTP path)."""
        return bool(self.path and self.path.startswith("/"))

    @property
    def blocks_completion(self) -> bool:
        """Must-have AND probeable — the set that gates the build (hard mode)."""
        return self.must_have and self.probeable

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "actor_role": self.actor_role,
            "action": self.action,
            "method": self.method,
            "path": self.path,
            "body_hint": dict(self.body_hint),
            "expect": self.expect,
            "must_have": self.must_have,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Capability | None:
        """Defensive: a malformed capability row reifies to ``None`` (dropped),
        never raises — a partial plan beats a sunk build (R-10)."""
        try:
            cid = _clean_str(d.get("id") or d.get("action"), limit=80)
            if not cid:
                return None
            role = _clean_str(d.get("actor_role"), limit=16).lower() or "user"
            if role not in _VALID_ROLES:
                role = "user"
            method = _clean_str(d.get("method"), limit=8).upper() or "POST"
            path = _clean_str(d.get("path"), limit=200)
            if path and not path.startswith("/"):
                path = ""  # only absolute API paths are probeable; drop junk
            body = d.get("body_hint")
            if not isinstance(body, dict):
                body = {}
            return cls(
                id=cid,
                actor_role=role,
                action=_clean_str(d.get("action"), limit=200),
                method=method,
                path=path,
                body_hint=body,
                expect=_clean_str(d.get("expect"), limit=16) or "2xx",
                must_have=bool(d.get("must_have", True)),
            )
        except Exception:
            return None


@dataclass(frozen=True)
class Screen:
    """A page the app must render. Coverage checks the file exists + GET ≠ 5xx."""

    route: str
    name: str = ""
    purpose: str = ""
    primary_entity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "name": self.name,
            "purpose": self.purpose,
            "primary_entity": self.primary_entity,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Screen | None:
        route = _clean_str(d.get("route"), limit=120)
        if not route:
            return None
        if not route.startswith("/"):
            route = "/" + route.lstrip("/")
        return cls(
            route=route,
            name=_clean_str(d.get("name"), limit=80),
            purpose=_clean_str(d.get("purpose"), limit=200),
            primary_entity=_clean_str(d.get("primary_entity"), limit=80) or None,
        )


@dataclass(frozen=True)
class Entity:
    """A data model the app needs. ``owner_scoped`` → must NOT leak across users
    (feeds the isolation expectation; see the isolation gate)."""

    name: str
    fields: tuple[str, ...] = ()
    owner_scoped: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": list(self.fields),
            "owner_scoped": self.owner_scoped,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Entity | None:
        name = _clean_str(d.get("name"), limit=80)
        if not name:
            return None
        raw_fields = d.get("fields") or ()
        if isinstance(raw_fields, str):
            raw_fields = re.split(r"[,;]", raw_fields)
        fields = tuple(
            f for f in (_clean_str(x, limit=60) for x in raw_fields) if f
        )[:24]
        return cls(
            name=name,
            fields=fields,
            owner_scoped=bool(d.get("owner_scoped", True)),
        )


@dataclass(frozen=True)
class BuildPlan:
    """The full pre-build feature spec. Empty → today's behaviour (no checklist,
    no coverage gate)."""

    summary: str = ""
    screens: tuple[Screen, ...] = ()
    entities: tuple[Entity, ...] = ()
    capabilities: tuple[Capability, ...] = ()
    acceptance: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.screens or self.entities or self.capabilities)

    def blocking_capabilities(self) -> tuple[Capability, ...]:
        """Must-have + probeable capabilities — the set the hard coverage gate
        refuses to ship without."""
        return tuple(c for c in self.capabilities if c.blocks_completion)

    def to_dict(self) -> dict[str, Any]:
        """JSON form for ``projects.discovery_spec['build_plan']``. Round-trips
        via :meth:`from_dict`."""
        return {
            "summary": self.summary,
            "screens": [s.to_dict() for s in self.screens],
            "entities": [e.to_dict() for e in self.entities],
            "capabilities": [c.to_dict() for c in self.capabilities],
            "acceptance": list(self.acceptance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BuildPlan:
        """Rebuild a plan from a persisted row. Defensive: a partial / legacy /
        malformed row degrades to an empty plan rather than raising, so a bad
        ``discovery_spec`` can never sink the build (R-10)."""
        if not data or not isinstance(data, dict):
            return cls()
        try:
            screens = tuple(
                s
                for s in (
                    Screen.from_dict(x)
                    for x in (data.get("screens") or [])
                    if isinstance(x, dict)
                )
                if s
            )[:_MAX_SCREENS]
            entities = tuple(
                e
                for e in (
                    Entity.from_dict(x)
                    for x in (data.get("entities") or [])
                    if isinstance(x, dict)
                )
                if e
            )[:_MAX_ENTITIES]
            caps = tuple(
                c
                for c in (
                    Capability.from_dict(x)
                    for x in (data.get("capabilities") or [])
                    if isinstance(x, dict)
                )
                if c
            )[:_MAX_CAPABILITIES]
            acc = tuple(
                a
                for a in (
                    _clean_str(x, limit=200) for x in (data.get("acceptance") or [])
                )
                if a
            )[:12]
            return cls(
                summary=_clean_str(data.get("summary"), limit=400),
                screens=screens,
                entities=entities,
                capabilities=caps,
                acceptance=acc,
            )
        except Exception as exc:
            log.warning("build_plan.from_dict degraded to empty: %r", exc)
            return cls()

    def checklist_block(self) -> str:
        """The plan rendered as the build-prompt checklist (and the human-facing
        contract). Empty plan → ``""`` so the caller appends nothing."""
        if self.is_empty:
            return ""
        lines: list[str] = [
            "\n\nПЛАН СБОРКИ (это ДОЛЖНО существовать к концу — НЕ останавливайся "
            "на пустом каркасе; собери ВЕСЬ план, потом проверь и заверши):",
        ]
        if self.summary:
            lines.append(f"СУТЬ: {self.summary}")
        if self.screens:
            lines.append("ЭКРАНЫ (каждый ОБЯЗАН существовать и открываться без ошибки):")
            for s in self.screens:
                tail = f" — {s.purpose}" if s.purpose else ""
                lines.append(f"  - {s.route} «{s.name}»{tail}")
        if self.entities:
            lines.append("СУЩНОСТИ (данные):")
            for e in self.entities:
                flds = ", ".join(e.fields[:10])
                lines.append(f"  - {e.name}({flds})")
        if self.capabilities:
            lines.append(
                "ВОЗМОЖНОСТИ (КАЖДАЯ обязана РЕАЛЬНО работать — вернуть свой "
                "ожидаемый статус своему актору; это и есть критерий «готово»):"
            )
            for c in self.capabilities:
                where = f" → {c.method} {c.path}" if c.path else " (через UI)"
                star = "" if c.must_have else " (доп.)"
                lines.append(
                    f"  - [{c.actor_role}] {c.action}{where} ⇒ {c.expect}{star}"
                )
        if self.blocking_capabilities():
            lines.append(
                "ПЕРЕД done: проверь КАЖДУЮ обязательную возможность инструментом "
                "`probe` (реальный авторизованный запрос) — она ОБЯЗАНА вернуть "
                "ожидаемый статус. Не вызывай done, пока probe каждой не зелёный."
            )
        lines.append(
            "НЕ вызывай done, пока каждый экран не существует и каждая "
            "обязательная возможность не отвечает ожидаемым статусом."
        )
        return "\n".join(lines)


def read_plan(discovery_spec: dict[str, Any] | None) -> BuildPlan:
    """Read the persisted plan out of a ``projects.discovery_spec`` JSONB row.

    Fail-soft: no spec, no ``build_plan`` key, or a malformed one → empty plan."""
    if not discovery_spec or not isinstance(discovery_spec, dict):
        return BuildPlan()
    return BuildPlan.from_dict(discovery_spec.get("build_plan"))


def merge_plan_into_spec(
    discovery_spec: dict[str, Any] | None, plan: BuildPlan
) -> dict[str, Any]:
    """Return a new ``discovery_spec`` dict carrying ``build_plan`` without
    disturbing the existing (FidelitySpec) keys. Empty plan → spec unchanged."""
    out = dict(discovery_spec or {})
    if not plan.is_empty:
        out["build_plan"] = plan.to_dict()
    return out


# ── planner pass ─────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """\
Ты — ПРОДУКТОВЫЙ АРХИТЕКТОР. До того как программист напишет код, ты превращаешь \
запрос пользователя в КОРОТКИЙ структурный план приложения: какие экраны, какие \
данные, и какие КОНКРЕТНЫЕ действия пользователь должен мочь выполнить (каждое — \
запрос, который ОБЯЗАН реально отработать).

Стек сборки: {stack}. Думай про реальные маршруты этого стека.

КОНВЕНЦИИ РОУТОВ (реальные пути стека, не выдумывай):
- nextjs-entities: сущность Name -> /api/entities/<Name> (GET/POST/PATCH/DELETE).
- fullstack / drizzle: пишешь сам, обычно /api/<resource> и /api/<resource>/[id].
- nextjs-realtime: сообщения /api/realtime/<channel>, каналы /api/channels.
- vite-react-spa: без бэка -> UI-действия (path: "", must_have: false).
Не уверен в пути — path: "" + must_have: false, не выдумывай эндпойнт.

Верни СТРОГО валидный JSON (и больше НИЧЕГО — без markdown-ограждений, без \
пояснений) по схеме:
{{
  "summary": "<одна строка: что это за приложение>",
  "screens": [
    {{"route": "/dashboard", "name": "Обзор", "purpose": "...", "primary_entity": null}}
  ],
  "entities": [
    {{"name": "Client", "fields": ["name", "phone", "status"], "owner_scoped": true}}
  ],
  "capabilities": [
    {{"id": "create_client", "actor_role": "user", "action": "создать клиента",
      "method": "POST", "path": "/api/clients", "body_hint": {{"name": "Иван"}},
      "expect": "2xx", "must_have": true}}
  ],
  "acceptance": ["<человеческий критерий готовности>"]
}}

ЖЁСТКИЕ ПРАВИЛА:
- НЕ БОЛЬШЕ {max_screens} экранов, {max_entities} сущностей, {max_caps} возможностей. \
Оставляй ТОЛЬКО то, что реально нужно ядру продукта — без добивки.
- capabilities — это сердце плана. Для каждой укажи КОНКРЕТНЫЙ method+path, если \
уверен в эндпойнте этого стека (тогда `must_have: true` — её будут проверять боевым \
запросом). Если действие чисто UI-шное и эндпойнт неочевиден — оставь `path: ""` и \
`must_have: false` (его не будут пробить запросом, только как ориентир).
- Добавь ХОТЯ БЫ ОДНУ негативную/изоляционную возможность, если есть приватные \
данные: чужой пользователь НЕ должен видеть чужое (`expect: "403"` или `"404"`).
- Реальные осмысленные имена и поля (по-русски в action/purpose), без «ваш текст».
- Только JSON. Первый символ ответа — `{{`."""


def _planner_system(stack: str) -> str:
    return _PLANNER_SYSTEM.format(
        stack=stack or "next.js",
        max_screens=_MAX_SCREENS,
        max_entities=_MAX_ENTITIES,
        max_caps=_MAX_CAPABILITIES,
    )


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_plan(raw: str | None) -> BuildPlan:
    """Parse the planner's raw text into a bounded :class:`BuildPlan`.

    Tolerant: strips code fences, finds the outermost ``{...}`` if the model
    wrapped the JSON in prose, and degrades to an empty plan on any failure."""
    text = (raw or "").strip()
    if not text:
        return BuildPlan()
    # Strip ```json fences if present.
    if text.startswith("```"):
        text = text.strip("`")
        text = re.sub(r"^json\s*", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(text)
        if not m:
            log.warning("build_plan: no JSON object in planner output")
            return BuildPlan()
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            log.warning("build_plan: planner JSON parse failed: %r", exc)
            return BuildPlan()
    if not isinstance(data, dict):
        return BuildPlan()
    return BuildPlan.from_dict(data)


async def plan_build(
    prompt: str,
    *,
    stack: str = "",
    model: str | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
) -> BuildPlan:
    """Run the planner pass → a bounded :class:`BuildPlan`.

    Returns an EMPTY plan (today's behaviour) when ``use_build_plan`` is off, in
    mock mode, or on any gateway/parse error — the feature is strictly additive
    and can never regress a build (R-10).
    """
    settings = get_settings()
    if not getattr(settings, "use_build_plan", False):
        return BuildPlan()
    prompt = (prompt or "").strip()
    if not prompt:
        return BuildPlan()
    use_model = model or model_for_role("planner")
    messages = [
        {"role": "system", "content": _planner_system(stack)},
        {"role": "user", "content": prompt[:6000]},
    ]
    try:
        raw = await complete_chat(
            messages,
            use_model,
            user_id=user_id,
            project_id=project_id,
            max_tokens=2048,
            temperature=0.0,
        )
    except LLMError as exc:
        log.warning("build_plan: planner gateway error → empty plan: %r", exc)
        return BuildPlan()
    except Exception as exc:
        log.warning("build_plan: planner unexpected error → empty plan: %r", exc)
        return BuildPlan()
    plan = parse_plan(raw)
    if plan.is_empty:
        log.info("build_plan: empty plan (mock/parse) — today's behaviour")
    else:
        log.info(
            "build_plan: screens=%d entities=%d caps=%d (blocking=%d)",
            len(plan.screens),
            len(plan.entities),
            len(plan.capabilities),
            len(plan.blocking_capabilities()),
        )
    return plan


__all__ = [
    "BuildPlan",
    "Capability",
    "Entity",
    "Screen",
    "merge_plan_into_spec",
    "parse_plan",
    "plan_build",
    "read_plan",
]
