"""One-time/idempotent repair for historical false-completed build runs."""

from __future__ import annotations

import asyncio

from omnia_api.services.generation_runs import reconcile_completed_build_runs


async def main() -> None:
    changed = await reconcile_completed_build_runs()
    print(f"generation-run reconciliation complete: changed={changed}")


if __name__ == "__main__":
    asyncio.run(main())
