#!/usr/bin/env python3
"""Require a fresh, exact-copy local restore verdict after off-host storage."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def restore_proven(status: dict, now: datetime) -> bool:
    """The API binds this verdict to the downloaded ciphertext hash/timestamp.

    This verifies local payload restoration, not offline-key decryption. A copy
    is uploaded before this gate so a failed proof never prevents preservation.
    """
    if status.get("restore_test_ok") is not True:
        return False
    try:
        created = datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
        tested = datetime.fromisoformat(status["restore_tested_at"].replace("Z", "+00:00"))
        return (
            created.tzinfo is not None
            and tested.tzinfo is not None
            and created <= tested
            and -300 <= (now - created).total_seconds() <= 108000
            and -300 <= (now - tested).total_seconds() <= 604800
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def main() -> int:
    try:
        status = json.loads(Path(sys.argv[1]).read_bytes())
        valid = isinstance(status, dict) and restore_proven(status, datetime.now(UTC))
    except (IndexError, OSError, ValueError):
        valid = False
    if not valid:
        print("Local restore proof is missing, failed, stale or incompatible", file=sys.stderr)
        return 1
    print("Exact-copy local database restore proof is fresh; offline decrypt is separate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
