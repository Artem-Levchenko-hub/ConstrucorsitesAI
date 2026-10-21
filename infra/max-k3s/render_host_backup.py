#!/usr/bin/env python3
"""Render only the production host backup payload, without provisioning PostgreSQL."""
from pathlib import Path
import re
import sys


def render_payload(source: str) -> str:
    blocks = re.findall(
        r"^cat > /usr/local/bin/max-backup\.sh <<'EOF'\n(.*?)\nEOF$",
        source, re.MULTILINE | re.DOTALL,
    )
    if len(blocks) != 1:
        raise ValueError("exactly one literal host backup payload is required")
    return blocks[0] + "\n"


def render_file(path: Path) -> str:
    return render_payload(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    source = Path(__file__).resolve().parent / "remote/40-postgres.sh"
    sys.stdout.buffer.write(render_file(source).encode("utf-8"))
