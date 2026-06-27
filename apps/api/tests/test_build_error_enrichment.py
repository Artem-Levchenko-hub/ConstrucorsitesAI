"""Harness-hardening: a failed build's observation gets the REAL exports of a
mis-imported @/ module attached, so a weak model fixes the import instead of
looping on a hallucinated name (the live «getChannels vs listUserChannels»
failure). A model is only as good as the feedback the harness gives it.
"""

from __future__ import annotations

import asyncio

from omnia_api.services import agent_builder as ab
from omnia_api.services import orchestrator_client


def test_enrich_attaches_real_exports(monkeypatch) -> None:
    async def _fake_read(project_id, slug, path):
        if path == "src/lib/channels.ts":
            return (
                "export async function listUserChannels() {}\n"
                "export function createChannel() {}\n"
            )
        return None

    monkeypatch.setattr(orchestrator_client, "agent_read_file", _fake_read)
    detail = (
        "src/app/(app)/dashboard/page.tsx(2,10): error TS2305: Module "
        "'@/lib/channels' has no exported member 'getChannels'."
    )
    out = asyncio.run(ab._enrich_build_failure(detail, "pid", "slug"))
    assert "listUserChannels" in out
    assert "createChannel" in out
    assert "ПОДСКАЗКА" in out
    assert "getChannels" not in out.split("ПОДСКАЗКА")[1]  # hint lists REAL names


def test_enrich_noop_without_app_module() -> None:
    detail = "src/x.tsx(1,1): error TS1234: some generic error"
    assert asyncio.run(ab._enrich_build_failure(detail, "pid", "slug")) == detail


def test_enrich_handles_cannot_find_module(monkeypatch) -> None:
    async def _fake_read(project_id, slug, path):
        if path == "src/lib/realtime/index.ts":
            return "export const hub = {};\nexport { openChannel as open } from './client';"
        return None

    monkeypatch.setattr(orchestrator_client, "agent_read_file", _fake_read)
    detail = "error TS2307: Cannot find module '@/lib/realtime' or its types."
    out = asyncio.run(ab._enrich_build_failure(detail, "pid", "slug"))
    assert "hub" in out
    assert "open" in out  # re-export name resolved
