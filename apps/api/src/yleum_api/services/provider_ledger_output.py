"""Keep provider identifiers out of stdout and protected against accidental overwrite."""

import json
import os
from pathlib import Path
from typing import Any


def write_restricted_report(path: Path, report: dict[str, Any]) -> None:
    data = json.dumps(report, ensure_ascii=True, indent=2).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
