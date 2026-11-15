import hashlib
import json

import pytest

from yleum_api.services import generation_deployment_drain as drain


@pytest.mark.parametrize("action,basis", [
    ("expired-provider-check", "expired_provider_reservation"),
    ("resume-expired-provider-check", "owned_expired_provider_capture"),
    ("end-expired-provider", "expired_provider_end"),
])
def test_exact_expired_provider_context(action, basis):
    request = {
        "version": 1,
        "release_sha": "a" * 40,
        "run_id": "11111111-1111-4111-8111-111111111111",
        "attempt_id": "22222222-2222-4222-8222-222222222222",
        "state_revision": 7,
        "context_sha256": "3" * 64,
        "source_sha256": "4" * 64,
        "clock_sha256": "5" * 64,
    }
    if action == "expired-provider-check":
        request.update(reader_release_sha="b" * 40, mode="eligibility")
    if action == "resume-expired-provider-check":
        request.update(owner_nonce="33333333-3333-4333-8333-333333333333",
                       core_capture_sha256="6" * 64, commerce_capture_sha256="7" * 64)
    parse = drain.ExpiredProviderReleaseRequest.parse
    raw = json.dumps(request)
    context = parse(raw, release_sha=request["release_sha"], action=action)
    assert context.basis == basis
    assert context.scope.state_revision == 7
    assert str(context.scope.run_id) == request["run_id"]
    assert context.mode == ("eligibility" if action == "expired-provider-check" else "settled")
    assert context.reader_release_sha == {
        "expired-provider-check": "b" * 40, "end-expired-provider": "a" * 40,
    }.get(action)
    assert context.scope_sha256 == hashlib.sha256(json.dumps(
        request, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    for tampered in (
        {**request, "state_revision": True}, {**request, "release_sha": "c" * 40},
        {**request, "run_id": "00000000-0000-0000-0000-000000000000"},
        {**request, "arbitrary": "denied"},
    ):
        with pytest.raises(ValueError):
            parse(json.dumps(tampered), release_sha=request["release_sha"], action=action)
    with pytest.raises(ValueError):
        parse(raw[:-1] + ',"version":1}', release_sha=request["release_sha"], action=action)
    if action == "expired-provider-check":
        with pytest.raises(ValueError):
            parse(json.dumps({**request, "reader_release_sha": request["release_sha"]}),
                  release_sha=request["release_sha"], action=action)
    if action == "resume-expired-provider-check":
        with pytest.raises(ValueError):
            parse(json.dumps({**request, "core_capture_sha256": "z" * 64}),
                  release_sha=request["release_sha"], action=action)
