"""Self-heal a failed PyInstaller/NSIS build: ask the exe_doctor model role for a
structured patch (extra hidden-imports / collect-all / requirements pin) and apply it
to the BuildSpec. Returns None when the model gives nothing usable (caller gives up)."""
from __future__ import annotations
import json
import re
from dataclasses import replace

from omnia_api.core.config import model_for_role
from omnia_api.services.exe_build import BuildSpec
from omnia_api.services.llm_client import LLMError, complete_chat

_PROMPT = """PyInstaller/NSIS build failed. Return ONLY JSON, no prose:
{{"hidden_imports": [..], "collect_all": [..], "requirements": "<full requirements.txt or null>"}}
Add only what the error implies. Error log tail:
{log}
Current hidden_imports={hi} collect_all={ca}."""


async def _ask_model(prompt: str) -> str:
    try:
        return await complete_chat(
            [{"role": "user", "content": prompt}],
            model=model_for_role("exe_doctor"),
            max_tokens=400,
            temperature=0.0,
        )
    except LLMError:
        return ""


async def heal(error_log: str, spec: BuildSpec, sources: dict[str, str]) -> BuildSpec | None:
    """Propose a BuildSpec patch for a failed build; returns None when no new info."""
    raw = await _ask_model(
        _PROMPT.format(
            log=error_log[-3000:],
            hi=spec.hidden_imports,
            ca=spec.collect_all,
        )
    )
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        patch = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(patch, dict):
        return None
    hi = sorted(set(spec.hidden_imports) | set(patch.get("hidden_imports") or []))
    ca = sorted(set(spec.collect_all) | set(patch.get("collect_all") or []))
    reqs = patch.get("requirements") or spec.requirements
    if hi == spec.hidden_imports and ca == spec.collect_all and reqs == spec.requirements:
        return None  # nothing new → don't loop pointlessly
    return replace(spec, hidden_imports=hi, collect_all=ca, requirements=reqs)
