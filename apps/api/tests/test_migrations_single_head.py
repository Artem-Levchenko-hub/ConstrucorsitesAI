"""Migration-chain safety guard (G008 — durable week-long builds).

Four agents push to `main`; the classic failure is two migrations declaring the
SAME `down_revision` (a fork) or a duplicate `revision` id — `alembic upgrade
head` then errors with "multiple heads" and a deploy can silently ship against a
half-migrated DB. This test makes that impossible to merge unnoticed: it parses
the migration chain statically (no DB needed) and asserts it is a single linear
line with exactly one head and one root.
"""

from __future__ import annotations

import re
from pathlib import Path

_VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
# Migrations use either bare (`revision = "x"`) or annotated
# (`revision: str = "x"`, `down_revision: Union[str, None] = "x"`) declarations —
# the optional `(?::[^=\n]+)?` swallows a type annotation. A root's
# `down_revision = None` (unquoted) intentionally does NOT match, so it maps to
# None — without that, the `None` inside `Union[str, None]` would mislead us.
_REV = re.compile(r'^revision\s*(?::[^=\n]+)?=\s*["\']([^"\']+)["\']', re.M)
_DOWN = re.compile(r'^down_revision\s*(?::[^=\n]+)?=\s*["\']([^"\']+)["\']', re.M)


def _chain() -> dict[str, str | None]:
    """Map each revision id -> its down_revision (None for the root)."""
    chain: dict[str, str | None] = {}
    for path in _VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        rev = _REV.search(text)
        if not rev:
            continue
        down = _DOWN.search(text)
        chain[rev.group(1)] = down.group(1) if down else None
    return chain


def test_revision_ids_are_unique() -> None:
    # A duplicate revision id is two files claiming the same node — ambiguous.
    files = list(_VERSIONS.glob("*.py"))
    revs: list[str] = []
    for path in files:
        m = _REV.search(path.read_text(encoding="utf-8"))
        if m:
            revs.append(m.group(1))
    assert len(revs) == len(set(revs)), "duplicate revision id(s) in migrations/"


def test_no_two_migrations_share_a_parent() -> None:
    # Two migrations with the same down_revision = a fork = "multiple heads".
    chain = _chain()
    parents = [d for d in chain.values() if d is not None]
    dupes = {p for p in parents if parents.count(p) > 1}
    assert not dupes, f"forked migration chain — these down_revisions are claimed twice: {dupes}"


def test_exactly_one_head() -> None:
    chain = _chain()
    assert chain, "no migrations found"
    downs = {d for d in chain.values() if d is not None}
    heads = [rev for rev in chain if rev not in downs]
    assert len(heads) == 1, f"expected exactly one head, found {sorted(heads)}"


def test_exactly_one_root() -> None:
    chain = _chain()
    roots = [rev for rev, down in chain.items() if down is None]
    assert len(roots) == 1, f"expected exactly one root, found {sorted(roots)}"


def test_chain_is_fully_connected() -> None:
    # Walk from the head down; every node must be reachable (no dangling parent
    # pointing at a revision that doesn't exist).
    chain = _chain()
    downs = {d for d in chain.values() if d is not None}
    head = next(rev for rev in chain if rev not in downs)
    seen: set[str] = set()
    node: str | None = head
    while node is not None:
        assert node in chain, f"down_revision points at unknown revision {node!r}"
        assert node not in seen, "cycle detected in migration chain"
        seen.add(node)
        node = chain[node]
    assert len(seen) == len(chain), (
        f"unreachable migrations: {sorted(set(chain) - seen)}"
    )
