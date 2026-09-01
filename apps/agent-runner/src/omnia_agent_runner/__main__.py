from __future__ import annotations

import asyncio

from .service import load_runner_service_from_env


def main() -> None:
    service = load_runner_service_from_env()
    try:
        asyncio.run(service.serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
