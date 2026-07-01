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

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from omnia_api.core.config import get_settings
from omnia_api.services.agent_builder import Action, AgentResult

log = structlog.get_logger(__name__)

_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 32000
_THINKING_BUDGET = 8000
_MAX_TOOL_RESULT_CHARS = 20000
_HTTP_TIMEOUT_S = 300.0
_CALL_RETRIES = 5  # oneprovider enforces a per-account concurrency cap (429)

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
    _tool("done", "Finish — the requested app is built AND the last build is clean.",
          {"summary": _STR}, ["summary"]),
]


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


def _text_of(content: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(b.get("text", "")) for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


_NATIVE_PREAMBLE = (
    "Ты — автономный инженер: строишь РАБОЧЕЕ приложение в этом проекте, как Claude "
    "Code. У тебя есть инструменты — вызывай их напрямую: read_file/list_dir/grep "
    "чтобы понять существующий код, write_file/edit_file чтобы писать, build чтобы "
    "проверить компиляцию, bash/read_logs чтобы проверить рантайм, docs для свежей "
    "документации библиотек. Думай сколько нужно. Цикл: пиши код → build → чини "
    "РЕАЛЬНЫЕ ошибки до чистоты → проверь что работает → done. Делай что задумал, "
    "пиши полноценно, без заглушек и TODO. Когда приложение собрано и build чистый — "
    "вызови done с кратким summary."
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
        "system": system,
        "tools": _TOOLS,
        "tool_choice": {"type": "auto"},
        "messages": convo,
    }
    last: Exception | None = None
    for attempt in range(_CALL_RETRIES):
        try:
            r = await client.post(url, json=payload, timeout=_HTTP_TIMEOUT_S)
            if r.status_code == 429 or (
                r.status_code >= 400 and "rate_limit" in r.text[:300]
            ):
                await asyncio.sleep(6.0 * (attempt + 1))
                last = RuntimeError(f"429 concurrency (attempt {attempt + 1})")
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            last = exc
            await asyncio.sleep(3.0 * (attempt + 1))
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
    fact-gate = the ``build`` tool. No lossy window (opus bills tokens; prompt-cache on
    the passthrough path is a later optimization).
    """
    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/messages"

    convo: list[dict[str, Any]] = [{"role": "user", "content": task}]
    written: dict[str, str] = {}
    last_build_ok: bool | None = None
    wrote_since_build = False
    done_rejections = 0
    _DONE_REJECT_CAP = 3

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
                return AgentResult(
                    done=(resp.get("stop_reason") == "end_turn"),
                    summary=_text_of(content) or "(no tool call)",
                    files=written, steps=step + 1, transcript=convo,
                    stop_reason="no_tool",
                )

            results: list[dict[str, Any]] = []
            done_summary: str | None = None
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
                if emit:
                    await emit("agent.step", {"step": step, "action": name, "path": action.path})
                try:
                    obs = await execute(action)
                except Exception as exc:  # a tool crash must not kill the build
                    obs = {"ok": False, "error": f"tool {name} crashed: {exc}"}

                if name == "write_file" and obs.get("ok"):
                    written[action.path] = action.args.get("content", "")
                    wrote_since_build = True
                elif name == "build":
                    last_build_ok = bool(obs.get("ok"))
                    wrote_since_build = False
                results.append(_obs_to_tool_result(tu_id, obs))

            if done_summary is not None:
                if emit:
                    await emit("agent.done", {"step": step, "files": len(written)})
                return AgentResult(
                    done=True, summary=done_summary, files=written,
                    steps=step + 1, transcript=convo, stop_reason="done",
                )
            convo.append({"role": "user", "content": results})

    return AgentResult(
        done=False, summary="hit step budget without calling done",
        files=written, steps=max_steps, transcript=convo, stop_reason="max_steps",
    )
