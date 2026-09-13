"""Verify or rotate an existing project's Vault-backed DEKs without printing secrets."""

import argparse
from uuid import UUID

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.services.app_data_keys import AppDataKeyError
from omnia_orchestrator.services.app_data_runtime import key_manager


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify", "rotate"))
    parser.add_argument("project_id", type=UUID)
    args = parser.parse_args()
    try:
        manager = key_manager(get_settings())
        if args.operation == "rotate":
            manager.rotate(str(args.project_id))
            print("Keys rotated. Reconcile the trusted core before new-version writes.")
        else:
            manager.prepare(str(args.project_id))
            print("All retained data keys decrypted successfully.")
        return 0
    except AppDataKeyError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
