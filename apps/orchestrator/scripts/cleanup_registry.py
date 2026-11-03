"""Observe or collect obsolete application manifests; dry run unless --apply.

The explicit controller coverage file and journals must be provisioned by the
installer. Recovery uses the same lifetime lock as collection and waits for the
original registry Deployment to become ready before returning.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Support direct execution from the installed production checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.services.registry_retention_coordinator import (
    RetentionConfig,
    run_retention,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument(
        "--journal-root",
        type=Path,
        default=Path("/opt/omnia-runtime/retention/registry"),
    )
    parser.add_argument(
        "--controllers-file",
        type=Path,
        default=Path("/opt/omnia-runtime/retention/controllers.json"),
    )
    parser.add_argument("--expected-controller-count", type=int, default=2)
    parser.add_argument("--report", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--recover-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        registry = settings.image_registry
        if "://" not in registry:
            registry = "https://" + registry
        report = asyncio.run(
            run_retention(
                RetentionConfig(
                    state_root=args.state_root or Path(settings.cell_state_path).parent,
                    journal_root=args.journal_root,
                    controllers_file=args.controllers_file,
                    expected_controller_count=args.expected_controller_count,
                    apply=args.apply,
                    recover_only=args.recover_only,
                    report=args.report,
                    registry_url=registry,
                    registry_username=settings.image_registry_username,
                    registry_password=settings.image_registry_password.get_secret_value(),
                    internal_token=settings.internal_token.get_secret_value(),
                    kubeconfig_path=settings.k8s_kubeconfig_path,
                    kube_context=settings.k8s_context or None,
                )
            )
        )
    except Exception:
        # Settings validation errors can contain environment values. Never print
        # raw exceptions or tracebacks from this operational entry point.
        report = {"version": 1, "status": "failed", "code": "configuration_unavailable"}
    print(json.dumps(report, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
