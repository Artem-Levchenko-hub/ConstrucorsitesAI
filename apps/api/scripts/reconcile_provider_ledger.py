"""Import an operator-verified normalized statement; never obtains browser credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import NoReturn

import asyncpg  # type: ignore[import-untyped]

from yleum_api.core.config import get_settings
from yleum_api.services.provider_ledger import (
    StatementError,
    import_statement,
    parse_statement,
    report_organization,
)
from yleum_api.services.provider_ledger_output import write_restricted_report


async def run(args: argparse.Namespace) -> int:
    # Validate the file/source/scope before opening any database connection.
    statement = None
    if not args.report_only:
        if args.normalized_statement is None or args.source is None:
            raise StatementError("statement_and_source_required")
        statement = parse_statement(
            args.normalized_statement.read_bytes(),
            expected_organization_id=args.expected_organization_id,
            source=args.source.read_bytes(),
        )
    connection = await asyncpg.connect(
        get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
        timeout=10,
        command_timeout=120,
    )
    try:
        report = (
            await import_statement(connection, statement)
            if statement is not None
            else await report_organization(connection, args.expected_organization_id)
        )
    finally:
        await connection.close()
    write_restricted_report(args.report_file, report)
    # No operation/ref/organization/user IDs or payloads in logs.
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "states",
                    "confirmed_expense_kopecks",
                    "linked_estimate_rub",
                    "estimate_difference_rub",
                    "unresolved_operations",
                    "customer_billing_modified",
                )
            }
        )
    )
    return 2 if report["unresolved_operations"] else 0


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default embeds all unknown argument values in stderr.
        raise StatementError("invalid_arguments")


def main() -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--expected-organization-id", required=True)
    parser.add_argument("--normalized-statement", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--report-file", type=Path, required=True)
    parser.add_argument("--report-only", action="store_true")
    try:
        args = parser.parse_args()
        return asyncio.run(run(args))
    except StatementError as exc:
        print(json.dumps({"error": str(exc)}))
        return 3
    except Exception as exc:
        # asyncpg and file errors can embed a DSN or private path. Never echo them.
        print(json.dumps({"error": "reconciliation_failed", "error_type": type(exc).__name__}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
