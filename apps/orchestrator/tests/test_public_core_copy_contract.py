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
            sources.extend(
                token
                for token in shlex.split(line)[1:-1]
                if token.startswith("scripts/public-max-core/")
            )
    assert sources, "Expected trusted runtime and build contracts"
    for relative in sources:
        assert (root / relative).is_file(), relative
        assert f"!{relative}" in rules, f"Missing COPY source in build context: {relative}"


def test_core_build_and_privilege_harness_select_the_same_migration_inventory():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "scripts/public-max-core/Dockerfile").read_text()
    workflow = (root.parents[1] / ".github/workflows/ci.yml").read_text()
    assert (
        "COPY --chown=node:node scripts/public-max-core/core-migrations.mjs "
        "./select-core-migrations.mjs"
        in dockerfile
    )
    assert "RUN node select-core-migrations.mjs drizzle" in dockerfile
    assert dockerfile.index("RUN node select-core-migrations.mjs drizzle") < dockerfile.index(
        "RUN node prepare-managed-core.mjs"
    )
    assert 'node scripts/public-max-core/core-migrations.mjs "$test_root/drizzle"' in workflow
    assert "node --test scripts/public-max-core/core-migrations.test.mjs" in workflow
