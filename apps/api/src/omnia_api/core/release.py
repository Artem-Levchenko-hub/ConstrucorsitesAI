from __future__ import annotations

import re

_RELEASE_SHA = re.compile(r"[0-9a-f]{7,40}")


def normalize_release_sha(value: str | None) -> str:
    return value if value is not None and _RELEASE_SHA.fullmatch(value) else "unknown"
