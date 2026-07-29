"""One-time/idempotent scrub for historical agent-step observations."""

from __future__ import annotations

import asyncio

from omnia_api.services.agent_progress import scrub_persisted_agent_steps


async def main() -> None:
    scanned, changed = await scrub_persisted_agent_steps()
    print(f"agent-step scrub complete: scanned={scanned} changed={changed}")


if __name__ == "__main__":
    asyncio.run(main())
