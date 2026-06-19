"""Hot-fork recap — the warm seed message that greets a remixer (pillar 4).

Two falsifiable layers:
  1. ``build_fork_recap`` (pure) — names the source, echoes the captured design
     DNA, and emits niche-native starter edits, all deterministic + LLM-free.
  2. The fork seam itself — ``POST /fork`` persists exactly ONE assistant recap
     row keyed to the fork, so the remixer's chat is WARM (never the cold
     generic "Поговорим о вашем сайте" empty state) and the source is untouched.
"""

import httpx
from sqlalchemy import select

from omnia_api.models.message import Message
from omnia_api.services.fork_recap import build_fork_recap


def test_recap_names_source_and_dna() -> None:
    spec = {
        "dark_mode": True,
        "primary_family": "violet",
        "sections": ["catalog"],
        "tone": "premium",
    }
    out = build_fork_recap("Кафе Юла", spec, design_preset_name="Restaurant Warm")
    assert out.startswith("<remix ")
    assert out.endswith("</remix>")
    assert 'name="Кафе Юла"' in out
    # DNA echoes the captured axes (niche from the name + theme + accent + tone).
    assert "кафе / ресторан" in out
    assert "тёмная тема" in out
    assert "фиолетовый акцент" in out
    assert "премиум" in out


def test_recap_starter_edits_are_niche_native() -> None:
    out = build_fork_recap("Кафе Юла", {"sections": []}, None)
    # A café gets a café-native first move, not a generic one.
    assert "меню" in out.lower()
    # Three starter edits → three body lines.
    body = out.split(">", 1)[1].rsplit("<", 1)[0]
    assert len([ln for ln in body.split("\n") if ln.strip()]) == 3


def test_recap_accent_swap_differs_from_current() -> None:
    # Current accent already emerald → swap nudge must pick a DIFFERENT colour.
    out = build_fork_recap("Магазин", {"primary_family": "emerald"}, None)
    assert "изумрудный" not in out.split('dna="', 1)[1].split('"', 1)[1].lower() or (
        "янтарный" in out.lower()
    )
    assert "янтарный" in out.lower()


def test_recap_degrades_gracefully_with_no_spec() -> None:
    out = build_fork_recap("Безымянный лендинг", None, "SaaS Product")
    assert out.startswith("<remix ")
    # No discovery + no known niche → preset name is the DNA fallback, never bare.
    assert "SaaS Product" in out


def test_recap_escapes_attribute_breakers() -> None:
    out = build_fork_recap('Сайт "ABC" <x>', {}, None)
    assert '<x>' not in out
    assert 'name=' in out  # still a valid single attribute, quotes neutralised


async def _register(client: httpx.AsyncClient, email: str) -> None:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": "secret123"}
    )
    assert r.status_code == 201


async def test_fork_seeds_one_warm_recap_message(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "recap-src@example.com")
    r = await client.post(
        "/api/projects", json={"name": "Кафе Юла", "template": "blank"}
    )
    assert r.status_code == 201, r.text
    source_id = r.json()["id"]

    r = await client.post(f"/api/projects/{source_id}/fork")
    assert r.status_code == 201, r.text
    fork_id = r.json()["id"]

    # The fork carries exactly ONE assistant row — the warm recap — so the chat
    # is never the cold empty state. The source stays chat-empty (untouched).
    fork_msgs = (
        await db_session.execute(
            select(Message).where(Message.project_id == fork_id)
        )
    ).scalars().all()
    assert len(fork_msgs) == 1
    seed = fork_msgs[0]
    assert seed.role == "assistant"
    assert seed.content.startswith("<remix ")
    assert "Кафе Юла" in seed.content
    # tokens_out is set (not None) so the client never treats the seed as a
    # mid-stream message; tokens_in stays None so no "0 tokens" footer shows.
    assert seed.tokens_out is not None
    assert seed.tokens_in is None

    source_msgs = (
        await db_session.execute(
            select(Message).where(Message.project_id == source_id)
        )
    ).scalars().all()
    assert len(source_msgs) == 0
