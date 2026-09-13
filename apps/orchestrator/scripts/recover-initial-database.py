"""Explicit historical bootstrap recovery; dry-run unless --apply is supplied."""

import argparse
import asyncio
import json
from uuid import UUID


async def run(args: argparse.Namespace) -> dict[str, object]:
    from omnia_orchestrator.core.config import get_settings
    from omnia_orchestrator.services.initial_database_recovery import recover_initial_database
    from omnia_orchestrator.services.workspace_provider_factory import (
        build_workspace_provider,
        settings_for_workspace,
    )

    settings = settings_for_workspace(get_settings(), args.workspace)
    provider = build_workspace_provider(settings)
    manager = getattr(provider, "resource_manager", None)
    if manager is None:
        raise RuntimeError("provider unavailable")
    return await recover_initial_database(
        manager=manager,
        workspace_id=args.workspace,
        expected_epoch=args.expected_epoch,
        initial_operation_id=args.initial_operation,
        apply=args.apply,
        expected_journal_digest=args.expected_journal_digest,
        expected_admission_digest=args.expected_admission_digest,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=UUID, required=True)
    parser.add_argument("--expected-epoch", type=int, required=True)
    parser.add_argument("--initial-operation", type=UUID, required=True)
    parser.add_argument("--expected-journal-digest")
    parser.add_argument("--expected-admission-digest")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and not (args.expected_journal_digest and args.expected_admission_digest):
        parser.error("--apply requires both digests returned by a dry-run")
    try:
        print(json.dumps(asyncio.run(run(args))))
    except Exception as exc:
        from omnia_orchestrator.services.initial_database_recovery import (
            InitialDatabaseRecoveryRejected,
        )

        reason = (
            str(exc) if isinstance(exc, InitialDatabaseRecoveryRejected) else "inspection_failed"
        )
        print(json.dumps({"status": "rejected", "reason": reason}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
