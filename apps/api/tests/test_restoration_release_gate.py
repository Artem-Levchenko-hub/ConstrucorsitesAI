"""A smoke result only certifies the revision it actually ran against.

Twice in September 2026 the production smoke was read as a verdict on a release it
had never seen: once because the expected variables were stale, once because the
canary was gone and two 502s hid everything else. These tests pin the identity gate:
the runner's own revision, five service revisions compared separately, and a missing
canary that can never be waved through.
"""

from __future__ import annotations

import json

import pytest

from yleum_api.ops.production_smoke import (
    ReleaseIdentity,
    smoke_artifact,
    validate_smoke_identity,
)

_SHA_A = "a" * 40
_SHA_B = "b" * 40


def _identity(**overrides: str) -> ReleaseIdentity:
    values = {
        "web": _SHA_A,
        "api": _SHA_A,
        "worker": _SHA_A,
        "generation_worker": _SHA_A,
        "orchestrator": _SHA_A,
    }
    values.update(overrides)
    return ReleaseIdentity(**values)


def test_matching_identity_on_the_same_runner_has_no_failures() -> None:
    assert validate_smoke_identity(_SHA_A, _identity(), _identity()) == []


def test_smoke_rejects_old_runner_for_current_release() -> None:
    # The workflow checked out an older revision than the release it is certifying.
    failures = validate_smoke_identity(_SHA_B, _identity(), _identity())

    assert failures == ["runner.release_mismatch"]


def test_smoke_rejects_independent_generation_worker_drift() -> None:
    # Both workers share one expected revision, but they are separate containers and
    # a drifted generation worker must not hide behind the ordinary worker.
    failures = validate_smoke_identity(
        _SHA_A, _identity(), _identity(generation_worker=_SHA_B)
    )

    assert failures == ["generation_worker.release_mismatch"]
    assert "worker.release_mismatch" not in failures


def test_every_service_is_compared_on_its_own() -> None:
    failures = validate_smoke_identity(
        _SHA_A, _identity(), _identity(web=_SHA_B, orchestrator=_SHA_B)
    )

    assert failures == ["orchestrator.release_mismatch", "web.release_mismatch"]


def test_an_unobserved_service_is_a_failure_not_a_pass() -> None:
    failures = validate_smoke_identity(_SHA_A, _identity(), _identity(api=None))  # type: ignore[arg-type]

    assert failures == ["api.release_mismatch"]


def test_expected_is_never_replaced_by_what_was_observed() -> None:
    expected = _identity()
    observed = _identity(api=_SHA_B)

    validate_smoke_identity(_SHA_A, expected, observed)

    # The dataclass is frozen, and the validator returns a verdict, not a new baseline.
    assert expected.api == _SHA_A
    with pytest.raises(Exception):
        expected.api = _SHA_B  # type: ignore[misc]


def test_missing_canary_cannot_skip_release_gate() -> None:
    artifact = smoke_artifact(
        runner_sha=_SHA_A,
        expected=_identity(),
        observed=_identity(),
        failures=["max_health.http_502", "max_webhook.http_502"],
        started_at="2026-09-22T06:00:00Z",
        finished_at="2026-09-22T06:00:04Z",
    )

    assert artifact["status"] == "failed"
    assert "max_health.http_502" in artifact["failures"]


def test_artifact_carries_both_identity_maps_and_the_runner() -> None:
    expected, observed = _identity(), _identity(web=_SHA_B)
    artifact = smoke_artifact(
        runner_sha=_SHA_B,
        expected=expected,
        observed=observed,
        # The record stores the verdict of the run; it does not re-decide it.
        failures=validate_smoke_identity(_SHA_B, expected, observed),
        started_at="2026-09-22T06:00:00Z",
        finished_at="2026-09-22T06:00:04Z",
    )

    assert artifact["runner_sha"] == _SHA_B
    assert artifact["expected"]["web"] == _SHA_A
    assert artifact["observed"]["web"] == _SHA_B
    assert set(artifact["expected"]) == {
        "web", "api", "worker", "generation_worker", "orchestrator",
    }
    assert artifact["started_at"] < artifact["finished_at"]
    # An identity mismatch found by the validator is folded into the same verdict.
    assert artifact["status"] == "failed"
    assert json.dumps(artifact)  # serialisable as written


def test_a_clean_run_is_the_only_passing_artifact() -> None:
    artifact = smoke_artifact(
        runner_sha=_SHA_A,
        expected=_identity(),
        observed=_identity(),
        failures=[],
        started_at="2026-09-22T06:00:00Z",
        finished_at="2026-09-22T06:00:04Z",
    )

    assert artifact["status"] == "passed" and artifact["failures"] == []


def test_a_runner_that_already_contains_the_release_may_certify_it() -> None:
    """Быть впереди — не то же самое, что быть не тем.

    22.09: после коммита с одной лишь документацией мониторинг покраснел, хотя на
    проде ничего не менялось. Правило сравнивало ревизии посимвольно, а защищать
    оно должно от другого — от прогона со СТАРОГО кода, чьи критерии не знают о
    новой поставке. Прогон, содержащий выкаченный код, знает о нём всё.

    Цена лишней строгости не теоретическая: мониторинг, краснеющий после каждого
    push, приучает не смотреть на красное.
    """
    failures = validate_smoke_identity(
        _SHA_B, _identity(), _identity(), runner_contains_release=True
    )

    assert failures == []


def test_a_runner_that_does_not_contain_the_release_still_cannot_certify_it() -> None:
    failures = validate_smoke_identity(
        _SHA_B, _identity(), _identity(), runner_contains_release=False
    )

    assert failures == ["runner.release_mismatch"]


def test_unknown_ancestry_is_refused_not_assumed() -> None:
    """Неизвестно — значит нет: иначе достаточно не передать признак."""
    failures = validate_smoke_identity(
        _SHA_B, _identity(), _identity(), runner_contains_release=None
    )

    assert failures == ["runner.release_mismatch"]


def test_an_exact_runner_needs_no_ancestry_evidence() -> None:
    assert validate_smoke_identity(_SHA_A, _identity(), _identity()) == []


def test_containing_the_release_never_excuses_a_drifted_service() -> None:
    # Послабление касается только ревизии прогона; сервисы сверяются как прежде.
    failures = validate_smoke_identity(
        _SHA_B, _identity(), _identity(web=_SHA_B), runner_contains_release=True
    )

    assert failures == ["web.release_mismatch"]
