import json
import os

import pytest

from yleum_api.services.generation import expired_provider_authorization as authority


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="Linux generation worker file")
def test_worker_scope_requires_regular_private_file_and_exact_release(tmp_path, monkeypatch):
    scope = tmp_path / "scope.json"
    release = "a" * 40
    payload = '{"version":1}'
    scope.write_text(json.dumps({"release_sha": release, "scope": payload}), encoding="utf-8")
    scope.chmod(0o600)
    monkeypatch.setenv("OMNIA_RELEASE_SHA", release)
    monkeypatch.setattr(authority, "_AUTHORIZATION", str(scope))
    original_fstat = os.fstat

    def owner(descriptor):
        info = list(original_fstat(descriptor))
        info[4] = 0
        return os.stat_result(info)

    monkeypatch.setattr(authority.os, "fstat", owner)
    assert authority.read_released_expired_provider_scope() == payload
    monkeypatch.setenv("OMNIA_RELEASE_SHA", "b" * 40)
    assert authority.read_released_expired_provider_scope() is None
    monkeypatch.setenv("OMNIA_RELEASE_SHA", release)
    scope.chmod(0o644)
    assert authority.read_released_expired_provider_scope() is None
    scope.chmod(0o600)
    link = tmp_path / "scope-link.json"
    link.symlink_to(scope)
    monkeypatch.setattr(authority, "_AUTHORIZATION", str(link))
    assert authority.read_released_expired_provider_scope() is None
