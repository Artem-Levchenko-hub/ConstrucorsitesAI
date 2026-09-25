"""A red canary must name the layer that is broken, or say it does not know.

On 21.09.2026 the production smoke was red for days with two 502s and nobody could
tell an outage from a monitoring URL pointing at a project that had been deleted.
These tests pin the classifier: it answers from observations per hop, it never
guesses, and it never carries a token, a body or an environment value into its
verdict.
"""

from __future__ import annotations

import json

import pytest

from yleum_api.ops.restoration_canary_diagnosis import diagnose_canary

_EXTERNAL_502 = {"hop": "external", "health": 502, "webhook": 502}


def test_runtime_ok_and_nginx_502_classifies_nginx() -> None:
    result = diagnose_canary(
        [
            {"hop": "external", "health": 502, "webhook": 502},
            {"hop": "nginx", "health": 502, "webhook": 502},
            {"hop": "runtime", "health": 200, "webhook": 401},
        ]
    )

    assert result["layer"] == "nginx"
    assert result["reason_code"] == "upstream_unavailable"


def test_missing_runtime_observation_is_unknown() -> None:
    assert diagnose_canary([{"hop": "external", "health": 502}])["layer"] == "unknown"


def test_a_route_that_exists_nowhere_is_a_stale_registration() -> None:
    # The live case: the edge answers 502, the host's nginx matches no server block
    # (its default server redirects), and no container or publication owns the host.
    result = diagnose_canary(
        [
            _EXTERNAL_502,
            {"hop": "nginx", "health": 301, "webhook": 301, "server_block": None},
            {"hop": "runtime", "container": None, "publication": None},
        ]
    )

    assert result["layer"] == "external_route"
    assert result["reason_code"] == "stale_route_registration"


def test_unhealthy_runtime_is_not_blamed_on_nginx() -> None:
    result = diagnose_canary(
        [
            _EXTERNAL_502,
            {"hop": "nginx", "health": 502, "webhook": 502, "server_block": "canary.conf"},
            {"hop": "runtime", "health": 503, "webhook": 503, "container": "omnia-machine-x-dev"},
        ]
    )

    assert result["layer"] == "runtime"
    assert result["reason_code"] == "runtime_unhealthy"


def test_auth_200_is_security_failure() -> None:
    # An unauthenticated webhook that answers 200 is worse than a red canary.
    result = diagnose_canary(
        [
            {"hop": "external", "health": 200, "webhook": 200},
            {"hop": "runtime", "health": 200, "webhook": 200, "container": "omnia-machine-x-dev"},
        ]
    )

    assert result["layer"] == "auth"
    assert result["reason_code"] == "webhook_accepts_unauthenticated"
    assert result["healthy"] is False


def test_everything_green_is_healthy_and_needs_no_fix() -> None:
    result = diagnose_canary(
        [
            {"hop": "external", "health": 200, "webhook": 401},
            {"hop": "runtime", "health": 200, "webhook": 401, "container": "omnia-machine-x-dev"},
        ]
    )

    assert (result["layer"], result["reason_code"]) == ("none", "healthy")
    assert result["healthy"] is True


def test_foreign_route_binding_rejected() -> None:
    # A healthy container that belongs to another project must stop the diagnosis
    # instead of being accepted as "the canary works".
    result = diagnose_canary(
        [
            {"hop": "external", "health": 200, "webhook": 401},
            {
                "hop": "runtime",
                "health": 200,
                "webhook": 401,
                "container": "omnia-machine-other",
                "expected_project": "11111111-1111-4111-8111-111111111111",
                "observed_project": "22222222-2222-4222-8222-222222222222",
            },
        ]
    )

    assert result["layer"] == "unknown"
    assert result["reason_code"] == "foreign_route_binding"
    assert result["healthy"] is False


@pytest.mark.parametrize("hop", ["external", "nginx", "runtime"])
def test_an_unknown_verdict_never_claims_a_fix(hop: str) -> None:
    result = diagnose_canary([{"hop": hop, "health": 502}])

    assert result["layer"] == "unknown"
    assert result["reason_code"] == "insufficient_observations"


def test_diagnostics_never_echo_env_or_body() -> None:
    secret = "shop-secret-value-not-for-logs"
    result = diagnose_canary(
        [
            {"hop": "external", "health": 502, "webhook": 502, "body": secret,
             "url": f"https://user:{secret}@canary.example/api/health"},
            {"hop": "nginx", "health": 502, "webhook": 502, "server_block": "x.conf",
             "authorization": f"Bearer {secret}"},
            {"hop": "runtime", "health": 503, "webhook": 503, "container": "c",
             "env": {"YOOKASSA_SECRET_KEY": secret}},
        ]
    )

    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert secret not in rendered
    assert "Bearer" not in rendered and "YOOKASSA" not in rendered
    # Only the agreed keys travel out of the classifier.
    assert set(result) == {
        "layer", "reason_code", "observed_codes", "release_sha", "evidence_digest", "healthy",
    }


def test_observed_codes_are_reported_per_hop_and_stay_numeric() -> None:
    result = diagnose_canary(
        [
            _EXTERNAL_502,
            {"hop": "runtime", "health": 200, "webhook": 401, "container": "c"},
        ],
        release_sha="0676e0be1234567890abcdef1234567890abcdef",
    )

    assert result["observed_codes"] == {
        "external": {"health": 502, "webhook": 502},
        "runtime": {"health": 200, "webhook": 401},
    }
    assert result["release_sha"] == "0676e0be1234567890abcdef1234567890abcdef"


def test_the_same_observations_always_digest_the_same() -> None:
    first = diagnose_canary([_EXTERNAL_502, {"hop": "runtime", "health": 200, "webhook": 401}])
    second = diagnose_canary([{"hop": "runtime", "webhook": 401, "health": 200}, _EXTERNAL_502])

    assert first["evidence_digest"] == second["evidence_digest"]
    assert len(str(first["evidence_digest"])) == 64


def test_a_malformed_observation_is_refused_not_guessed() -> None:
    for broken in ([], [{"health": 200}], [{"hop": "moon", "health": 200}], "nonsense"):
        result = diagnose_canary(broken)  # type: ignore[arg-type]
        assert result["layer"] == "unknown", broken
        assert result["healthy"] is False
