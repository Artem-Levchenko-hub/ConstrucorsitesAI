"""Every trusted script copied by the offline core build must enter its context."""

import shlex
from pathlib import Path


def test_public_core_copy_sources_are_explicitly_allowed():
    root = Path(__file__).resolve().parents[1]
    folder = root / "scripts/public-max-core"
    rules = (folder / "Dockerfile.dockerignore").read_text().splitlines()
    assert rules[0] == "**", "Core build uses a deny-by-default context"
    sources = []
    for line in (folder / "Dockerfile").read_text().splitlines():
        if line.startswith("COPY "):
            sources.extend(token for token in shlex.split(line)[1:-1]
                           if token.startswith("scripts/public-max-core/"))
    assert sources, "Expected trusted runtime and build contracts"
    for relative in sources:
        assert (root / relative).is_file(), relative
        assert f"!{relative}" in rules, f"Missing COPY source in build context: {relative}"
