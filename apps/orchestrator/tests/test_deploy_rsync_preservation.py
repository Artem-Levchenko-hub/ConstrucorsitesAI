"""Check the actual release transfer's filters against host-local collision paths."""

import fnmatch
import re
import shlex
from pathlib import Path

import pytest


def _transfer_args() -> list[str]:
    script = Path(__file__).resolve().parents[3] / "infra/release/deploy-prod.sh"
    transfers = re.findall(
        r"rsync\s+[^;\n]+commerce:/opt/omnia/", script.read_text(encoding="utf-8")
    )
    assert len(transfers) == 1, "review every core-to-commerce transfer"
    return shlex.split(transfers[0])


@pytest.mark.parametrize(
    ("path", "protected"),
    [
        (".deploy.lock", True),
        (".deploy.lock.stale-native-amocrm-20260929T090853Z", True),
        ("apps/orchestrator/.env", True),
        ("apps/orchestrator/.env.before-generation-contract-20260929T105620Z", True),
        ("apps/llm-gateway/deploy/full/.env.before-render-fixture-20260929T114634Z", True),
        ("apps/orchestrator/.env.example", False),
        ("apps/web/.env.local.example", False),
        ("apps/llm-gateway/src/yleum_gateway/services/cache.py", False),
    ],
)
def test_transfer_preserves_host_local_files_without_hiding_release_files(path, protected):
    args = _transfer_args()
    patterns = [args[index + 1] for index, arg in enumerate(args) if arg == "--exclude"]
    # These filters use root-anchored paths or slash-free basename patterns,
    # the two rsync matching forms used by the production transfer.
    excluded = any(
        fnmatch.fnmatchcase("/" + path, pattern)
        if pattern.startswith("/")
        else any(fnmatch.fnmatchcase(part, pattern) for part in path.split("/"))
        for pattern in patterns
    )
    assert excluded is protected, f"transfer protection mismatch for {path}"


def test_transfer_does_not_delete_commerce_only_files():
    args = _transfer_args()
    assert args[-2:] == ["/opt/omnia/", "commerce:/opt/omnia/"]
    assert not any(arg.startswith("--delete") for arg in args)
