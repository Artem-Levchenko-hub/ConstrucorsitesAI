from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnia_api.routers.max_studio import _current_snapshot_is_published
from omnia_api.services.deploy_attestation import DeployProof


@pytest.mark.parametrize(
    ("deployment", "proof"),
    [
        (
            {
                "phase": "done",
                "prod_url": "https://app.example",
                "commit_sha": "b" * 40,
                # A later completion time must not make another revision published.
                "finished_at": "2099-01-01T00:00:00+00:00",
            },
            DeployProof(True, "proven", commit_sha="a" * 40),
        ),
        (
            {
                "phase": "done",
                "prod_url": "https://app.example",
                "commit_sha": "a" * 40,
            },
            DeployProof(False, "attestation_missing", commit_sha="a" * 40),
        ),
        (
            {
                "phase": "done",
                "prod_url": "https://app.example",
            },
            DeployProof(True, "proven", commit_sha="a" * 40),
        ),
    ],
)
def test_max_readiness_rejects_unproven_or_nonexact_deploy(
    deployment: dict[str, str], proof: DeployProof
) -> None:
    snapshot = SimpleNamespace(commit_sha="a" * 40)

    assert not _current_snapshot_is_published(deployment, snapshot, proof)


def test_max_readiness_accepts_only_exact_deployed_proven_snapshot() -> None:
    sha = "a" * 40

    assert _current_snapshot_is_published(
        {"phase": "done", "prod_url": "https://app.example", "commit_sha": sha},
        SimpleNamespace(commit_sha=sha),
        DeployProof(True, "proven", commit_sha=sha),
    )
