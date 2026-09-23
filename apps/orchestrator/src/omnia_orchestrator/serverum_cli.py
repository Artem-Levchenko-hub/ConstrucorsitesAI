"""Serverum VPS ordering from the command line (`python -m omnia_orchestrator.serverum_cli`).

    serverum_cli.py [--dry-run] [--json] plans
    serverum_cli.py [--dry-run] [--json] servers
    serverum_cli.py [--dry-run] [--json] order --plan ID --hostname cells3 [--os …] [--location …]
                                                [--ssh-key-file ~/.ssh/id_ed25519.pub …] [--wait]
    serverum_cli.py [--dry-run] [--json] status SERVER_ID
    serverum_cli.py [--dry-run] [--json] wait-ip SERVER_ID [--timeout 600] [--poll 10]
    serverum_cli.py [--dry-run] delete SERVER_ID --yes

Reads SERVERUM_API_BASE_URL / SERVERUM_API_TOKEN / SERVERUM_ENDPOINTS_JSON from the
environment or ``.env`` (see services/serverum.py — endpoints «уточнить»).
``--dry-run`` needs no token and no network. Exit codes: 0 ok, 2 usage or missing
token, 3 provider error, 4 the server did not get ready in time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from omnia_orchestrator.services.serverum import (
    OrderRequest,
    ServerumAuthError,
    ServerumClient,
    ServerumError,
    ServerumPlan,
    ServerumServer,
    ServerumSettings,
    ServerumTimeout,
    client_from_settings,
    read_ssh_public_keys,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serverum_cli", description="Serverum VPS: plans, order, status, delete"
    )
    parser.add_argument("--dry-run", action="store_true", help="no network, synthetic answers")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plans", help="list VPS plans")
    sub.add_parser("servers", help="list servers of the account")
    order = sub.add_parser("order", help="order a VPS")
    order.add_argument("--plan", required=True, help="plan id (see `plans`)")
    order.add_argument("--hostname", required=True, help="lowercase name, e.g. cells3")
    order.add_argument("--os", default=None, help="OS image (default SERVERUM_DEFAULT_OS)")
    order.add_argument("--location", default=None, help="default SERVERUM_DEFAULT_LOCATION")
    order.add_argument(
        "--ssh-key-file", action="append", default=[], help="public key file(s) to install"
    )
    order.add_argument("--note", default="", help="free-form note for the order")
    order.add_argument("--wait", action="store_true", help="block until the server has an IP")
    order.add_argument("--timeout", type=float, default=600.0)
    order.add_argument("--poll", type=float, default=10.0)
    status = sub.add_parser("status", help="show one server")
    status.add_argument("server_id")
    wait = sub.add_parser("wait-ip", help="poll until the server is active with an IP")
    wait.add_argument("server_id")
    wait.add_argument("--timeout", type=float, default=600.0)
    wait.add_argument("--poll", type=float, default=10.0)
    delete = sub.add_parser("delete", help="delete a server (irreversible)")
    delete.add_argument("server_id")
    delete.add_argument("--yes", action="store_true", help="confirm the deletion")
    return parser


def _print_plans(plans: Sequence[ServerumPlan], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([plan.as_dict() for plan in plans], ensure_ascii=False, indent=2))
        return
    print(f"{'id':<16} {'vCPU':>4} {'RAM MB':>7} {'disk GB':>7} {'₽/мес':>9}  name")
    for plan in plans:
        price = "—" if plan.price_month_rub is None else f"{plan.price_month_rub:.0f}"
        print(
            f"{plan.id:<16} {plan.vcpu or '—':>4} {plan.ram_mb or '—':>7} "
            f"{plan.disk_gb or '—':>7} {price:>9}  {plan.name}"
        )


def _print_servers(servers: Sequence[ServerumServer], *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                [server.as_dict() for server in servers]
                if len(servers) != 1
                else servers[0].as_dict(),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(f"{'id':<16} {'status':<13} {'public ip':<16} {'private ip':<16} hostname")
    for server in servers:
        print(
            f"{server.id:<16} {server.status:<13} {server.public_ip or '—':<16} "
            f"{server.private_ip or '—':<16} {server.hostname or '—'}"
        )


async def _run(args: argparse.Namespace, client: ServerumClient, settings: ServerumSettings) -> int:
    async with client:
        if args.command == "plans":
            _print_plans(await client.list_plans(), as_json=args.json)
        elif args.command == "servers":
            _print_servers(await client.list_servers(), as_json=args.json)
        elif args.command == "order":
            request = OrderRequest(
                plan_id=args.plan,
                hostname=args.hostname,
                os=args.os or settings.default_os,
                location=args.location or settings.default_location,
                ssh_public_keys=read_ssh_public_keys(args.ssh_key_file),
                note=args.note,
            )
            server = await client.order_server(request)
            if args.wait:
                server = await client.wait_for_ip(
                    server.id, timeout_seconds=args.timeout, poll_seconds=args.poll
                )
            _print_servers([server], as_json=args.json)
        elif args.command == "status":
            _print_servers([await client.get_server(args.server_id)], as_json=args.json)
        elif args.command == "wait-ip":
            server = await client.wait_for_ip(
                args.server_id, timeout_seconds=args.timeout, poll_seconds=args.poll
            )
            _print_servers([server], as_json=args.json)
        elif args.command == "delete":
            if not args.yes:
                print("refusing to delete without --yes", file=sys.stderr)
                return 2
            await client.delete_server(args.server_id)
            if args.json:
                print(json.dumps({"deleted": args.server_id}))
            else:
                print(f"deleted {args.server_id}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = ServerumSettings()
    try:
        client = client_from_settings(settings, dry_run=args.dry_run)
    except ServerumAuthError as exc:
        print(f"serverum: {exc} (export SERVERUM_API_TOKEN or use --dry-run)", file=sys.stderr)
        return 2
    except ServerumError as exc:
        print(f"serverum: {exc}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(args, client, settings))
    except ServerumTimeout as exc:
        print(f"serverum: {exc}", file=sys.stderr)
        return 4
    except ServerumError as exc:
        print(f"serverum: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
