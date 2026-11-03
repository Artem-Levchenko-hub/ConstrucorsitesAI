"""Quiet CLI: one normally authenticated catalog browser acceptance, no messages."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from yleum_api.ops.production_canary import CanaryConfig
from yleum_api.ops.production_catalog_canary import CatalogEvidence, run_catalog_canary


def main() -> int:
    evidence = CatalogEvidence()
    directory = os.getenv("PRODUCTION_CATALOG_ARTIFACT_DIR")
    try:
        if not directory:
            evidence.fail("configuration_invalid")
        else:
            evidence = run_catalog_canary(CanaryConfig.from_env(), Path(directory))
    except BaseException:
        # Playwright exceptions can include login inputs, response bodies and
        # signed URLs. Only our closed vocabulary may enter logs/artifacts.
        evidence.fail("browser_failed")
    try:
        if directory:
            evidence.write(Path(directory))
        report = evidence.report()
        result = os.getenv("PRODUCTION_CANARY_RESULT_FILE")
        if result:
            Path(result).write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    except Exception:
        evidence.fail("artifact_failed")
        report = evidence.report()
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
