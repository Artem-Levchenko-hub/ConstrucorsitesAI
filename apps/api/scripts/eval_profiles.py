"""Price/quality eval harness for the role-orchestration model mix.

Searches for the "ideal price/quality formula" (Prompt Engineering for LLMs,
ch. 10: offline evaluation) by running a set of golden prompts through several
candidate ROLE profiles and scoring each on quality, cost and latency.

For each (profile × prompt) it runs the real Director→Polish pipeline:

    build_messages → director_polish_generate(director_model, polish_model)
      → parse PageIR JSON → apply_smart_defaults → render_page
      → ui_audit.audit (10-pt rubric) + dead-link count + cost + latency

then prints a markdown table and the Pareto-optimal profile (cheapest that
clears the quality bar). Generations run as `free` so the harness never debits
a wallet.

Run (needs a live LLM gateway with PROXYAPI_API_KEY; MOCK_LLM must be off):

    cd apps/api
    PYTHONPATH=src MOCK_LLM=false python scripts/eval_profiles.py
    PYTHONPATH=src MOCK_LLM=false python scripts/eval_profiles.py --prompts 5 --quality-bar 7

This is a measurement tool, not part of the request path — keep it dependency-light.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

# Make the src-layout package importable when run as `python scripts/eval_profiles.py`.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from omnia_api.core.config import ROLE_MODEL_MAP, get_settings  # noqa: E402
from omnia_api.sections.defaults import apply_smart_defaults  # noqa: E402
from omnia_api.sections.ir import PageIR  # noqa: E402
from omnia_api.sections.renderer import render_page  # noqa: E402
from omnia_api.services.director_polish import director_polish_generate  # noqa: E402
from omnia_api.services.link_validator import find_dead_links  # noqa: E402
from omnia_api.services.llm_client import set_free_generation  # noqa: E402
from omnia_api.services.prompt_builder import build_messages  # noqa: E402
from omnia_api.services.ui_audit import audit as ui_audit  # noqa: E402

# ── Golden prompts — representative of what real users ask for ───────────────
GOLDEN_PROMPTS: list[tuple[str, str]] = [
    ("coffee", "Лендинг для кофейни в центре города: меню, цены, форма брони столика."),
    ("dental", "Сайт стоматологической клиники: услуги, врачи, цены в рублях, запись."),
    ("saas", "Лендинг SaaS-сервиса для автоматизации отчётов: тарифы, фичи, FAQ, CTA."),
    ("portfolio", "Портфолио фотографа: галерея, обо мне, услуги, контакты."),
    ("fitness", "Сайт фитнес-студии: расписание, абонементы, тренеры, отзывы."),
    ("law", "Сайт юридической фирмы: практики, кейсы, команда, консультация."),
    ("restaurant", "Сайт ресторана авторской кухни: меню, бронь, события, доставка."),
    ("ecom", "Лендинг бренда косметики: каталог, состав, отзывы, доставка по РФ."),
    ("realestate", "Лендинг ЖК бизнес-класса: планировки, цены, локация, ипотека."),
    ("edu", "Лендинг онлайн-курса по дизайну: программа, цены, преподаватели, FAQ."),
]

# ── Candidate profiles — {profile_name: (director_model, polish_model)} ──────
# topmix-v1 mirrors ROLE_MODEL_MAP. The others probe cheaper/pricier corners so
# the table shows the cost/quality frontier.
PROFILES: dict[str, tuple[str, str]] = {
    "topmix-v1": (ROLE_MODEL_MAP["director"], ROLE_MODEL_MAP["polish"]),
    "all-deepseek": ("deepseek-chat", "deepseek-chat"),
    "deepseek-reasoner-director": ("deepseek-reasoner", "deepseek-chat"),
    "all-opus": ("claude-opus-4-7", "claude-opus-4-7"),
    "sonnet-director": ("claude-sonnet-4-6", "deepseek-chat"),
    "all-haiku": ("claude-haiku-4-5", "claude-haiku-4-5"),
}


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


async def _run_one(
    prompt: str, director_model: str, polish_model: str
) -> dict[str, float | int | bool]:
    """Run one generation and return its metrics."""
    messages = build_messages(
        current_files={},
        history=[],
        user_prompt=prompt,
        template="blank",
        preset_id=None,
        image_gen_enabled=False,
        project_id=str(uuid.uuid4()),
        model_id=director_model,
    )
    set_free_generation(True)  # never debit a wallet from the harness

    acc: list[str] = []
    cost = 0.0
    started = time.monotonic()
    async for event in director_polish_generate(
        base_messages=messages,
        user_prompt=prompt,
        director_model=director_model,
        polish_model=polish_model,
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
    ):
        if delta := event.get("delta"):
            acc.append(delta)
        if u := event.get("usage"):
            cost = float(u.get("cost_rub", 0.0))
        if event.get("error"):
            return {"valid": False, "score": 0, "cost_rub": cost,
                    "latency_s": round(time.monotonic() - started, 1), "dead_links": 99}
    latency = round(time.monotonic() - started, 1)

    # Parse → validate → render → audit.
    try:
        ir = PageIR.model_validate(json.loads(_strip_fence("".join(acc))))
        ir = apply_smart_defaults(ir, preset_id=None)
        html = render_page(ir)
    except Exception:
        return {"valid": False, "score": 0, "cost_rub": cost,
                "latency_s": latency, "dead_links": 99}

    files = {"src/index.html": html}
    report = ui_audit(files)
    dead = len(find_dead_links(files))
    return {
        "valid": True,
        "score": report.score,
        "cost_rub": round(cost, 4),
        "latency_s": latency,
        "dead_links": dead,
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description="Role-mix price/quality eval")
    ap.add_argument("--prompts", type=int, default=len(GOLDEN_PROMPTS),
                    help="how many golden prompts to run (default: all)")
    ap.add_argument("--quality-bar", type=int, default=7,
                    help="min avg audit score (0-10) a profile must clear")
    ap.add_argument("--profiles", type=str, default="",
                    help="csv subset of profile names (default: all)")
    args = ap.parse_args()

    if get_settings().mock_llm:
        print("REFUSING: MOCK_LLM is on — set MOCK_LLM=false and configure the "
              "gateway (PROXYAPI_API_KEY) so the harness measures real models.")
        return

    prompts = GOLDEN_PROMPTS[: args.prompts]
    wanted = {p.strip() for p in args.profiles.split(",") if p.strip()}
    profiles = {k: v for k, v in PROFILES.items() if not wanted or k in wanted}

    rows: list[dict] = []
    for name, (director_model, polish_model) in profiles.items():
        results = []
        for pname, prompt in prompts:
            r = await _run_one(prompt, director_model, polish_model)
            print(f"  [{name}] {pname}: score={r['score']}/10 "
                  f"cost=₽{r['cost_rub']} {r['latency_s']}s "
                  f"dead={r['dead_links']} valid={r['valid']}")
            results.append(r)
        n = len(results) or 1
        avg_score = sum(float(r["score"]) for r in results) / n
        avg_cost = sum(float(r["cost_rub"]) for r in results) / n
        avg_lat = sum(float(r["latency_s"]) for r in results) / n
        valid_pct = 100.0 * sum(1 for r in results if r["valid"]) / n
        rows.append({
            "profile": name, "director": director_model, "polish": polish_model,
            "avg_score": round(avg_score, 2), "avg_cost": round(avg_cost, 4),
            "avg_latency": round(avg_lat, 1), "valid_pct": round(valid_pct),
        })

    print("\n## Eval results\n")
    print("| profile | director -> polish | avg score | avg RUB | avg s | valid% |")
    print("|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (-x["avg_score"], x["avg_cost"])):
        print(f"| {r['profile']} | {r['director']} -> {r['polish']} | "
              f"{r['avg_score']}/10 | {r['avg_cost']} | {r['avg_latency']} | "
              f"{r['valid_pct']}% |")

    # Pareto pick: cheapest profile that clears the quality bar.
    cleared = [r for r in rows if r["avg_score"] >= args.quality_bar]
    if cleared:
        best = min(cleared, key=lambda x: x["avg_cost"])
        print(f"\n**Pareto pick** (>= {args.quality_bar}/10, cheapest): "
              f"`{best['profile']}` — {best['avg_score']}/10 at RUB {best['avg_cost']}/gen.")
    else:
        print(f"\nNo profile cleared the quality bar (>= {args.quality_bar}/10). "
              f"Lower --quality-bar or revise ROLE_MODEL_MAP.")


if __name__ == "__main__":
    asyncio.run(main())
