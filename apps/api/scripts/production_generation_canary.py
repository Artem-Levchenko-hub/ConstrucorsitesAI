from __future__ import annotations

import json
import sys

from omnia_api.ops.production_canary import (
    CanaryConfig,
    CanaryConfigurationError,
    CanaryFailure,
    ProductionCanary,
)


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, separators=(",", ":"), sort_keys=True), flush=True)


def main() -> int:
    try:
        config = CanaryConfig.from_env()
        ProductionCanary(config, emit=_emit).run()
    except CanaryConfigurationError:
        print("production canary configuration invalid", file=sys.stderr)
        return 1
    except CanaryFailure as exc:
        print(exc.public_message, file=sys.stderr)
        return 1
    except Exception:
        print("production canary failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
