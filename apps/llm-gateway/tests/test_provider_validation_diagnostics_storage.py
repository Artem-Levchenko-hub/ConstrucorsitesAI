"""Linux filesystem/security checks import only the helper, no Gateway startup."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest

from yleum_gateway.services import provider_validation_diagnostics as diagnostics

_KEY = "fixture-provider-key+/=NeverARealCredential"
_RESPONSE = b'{"error":{"code":"validation_error","detail":[{"loc":["body","messages",1],"type":"value_error"}]}}'

def _error(body=b'{"messages":[{"role":"user","content":"synthetic"}]}', *, status=422,
           url="https://api.llmgw.ru/v1/chat/completions", received=_RESPONSE):
    request = httpx.Request("POST", url, content=body, headers={"authorization": f"Bearer {_KEY}"})
    response = httpx.Response(status, content=received, request=request)
    return httpx.HTTPStatusError("synthetic status", request=request, response=response)


@pytest.fixture
def private_arm(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("Linux dirfd/O_NOFOLLOW/ownership contract; no Windows security fallback")
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    project, nonce = uuid4(), uuid4()
    arm = {"version": 1, "project_id": str(project), "nonce": str(nonce),
           "expires_at": (datetime.now(UTC) + timedelta(minutes=20)).isoformat()}
    path = directory / "armed.json"
    path.write_text(json.dumps(arm), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(diagnostics, "_DIRECTORY", directory)
    return directory, path, arm, {"project_id": project, "run_id": uuid4(), "call_id": uuid4()}


def _capture(scope, error=None):
    with diagnostics.provider_validation_scope(**scope):
        diagnostics.capture_validation_failure(error or _error(), credential=_KEY)


def test_disabled_never_creates_directory(tmp_path, monkeypatch):
    directory = tmp_path / "does-not-exist"
    monkeypatch.setattr(diagnostics, "_DIRECTORY", directory)
    _capture({"project_id": uuid4(), "run_id": uuid4(), "call_id": uuid4()})
    assert not directory.exists()


async def test_scope_is_task_local_and_survives_to_thread():
    first = {"project_id": uuid4(), "run_id": uuid4(), "call_id": uuid4()}
    second = {"project_id": uuid4(), "run_id": uuid4(), "call_id": uuid4()}

    async def observe(scope):
        with diagnostics.provider_validation_scope(**scope):
            await asyncio.sleep(0)
            return await asyncio.to_thread(diagnostics._SCOPE.get)

    observed = await asyncio.gather(observe(first), observe(second))
    assert [item.project_id for item in observed] == [str(first["project_id"]), str(second["project_id"])]
    assert diagnostics._SCOPE.get() is None


def test_existing_directory_without_arm_creates_nothing(private_arm):
    directory, path, arm, scope = private_arm
    path.unlink()
    _capture(scope)
    assert not list(directory.iterdir())


@pytest.mark.parametrize("scope_field", ["project_id", "run_id", "call_id"])
def test_invalid_scope_cannot_reuse_outer_scope(scope_field, monkeypatch):
    seen = []
    monkeypatch.setattr(diagnostics, "_open_directory", lambda: seen.append(True))
    good = {"project_id": uuid4(), "run_id": uuid4(), "call_id": uuid4()}
    with diagnostics.provider_validation_scope(**good):
        _capture({**good, scope_field: "../../outside"})
        assert diagnostics._SCOPE.get().project_id == str(good["project_id"])
    assert not seen and diagnostics._SCOPE.get() is None


@pytest.mark.parametrize("url,status", [
    ("https://api.llmgw.ru/v1/chat/completions", 503),
    ("https://elsewhere.invalid/v1/chat/completions", 422),
    ("http://api.llmgw.ru/v1/chat/completions", 422),
    ("https://api.llmgw.ru/v1/messages", 422),
    ("https://api.llmgw.ru/v1/chat/completions?key=private", 422),
    ("https://user:password@api.llmgw.ru/v1/chat/completions", 422),
])
def test_only_exact_provider_chat_422_is_eligible(monkeypatch, url, status):
    seen = []
    monkeypatch.setattr(diagnostics, "_open_directory", lambda: seen.append(True))
    _capture({"project_id": uuid4(), "run_id": uuid4(), "call_id": uuid4()}, _error(url=url, status=status))
    assert not seen


def test_redaction_preserves_original_body_hash_and_marks_changes():
    encoded = [*_sorted_forms(_KEY), f'Bearer {_KEY}']
    raw = json.dumps({"messages": encoded, "api_key": "unlisted-credential",
                      "max_tokens": 100}, ensure_ascii=False).encode()
    secrets = diagnostics._secret_forms(_KEY)
    result = diagnostics._body(raw, secrets, within_budget=True)
    assert result["sha256"] == hashlib.sha256(raw).hexdigest() and result["bytes"] == len(raw)
    assert result["content_state"] == "redacted"
    assert "unlisted-credential" not in result["utf8"]
    for value in _sorted_forms(_KEY):
        assert value not in result["utf8"]
    assert '"max_tokens": 100' in result["utf8"]


def _sorted_forms(key):
    return [key, quote(key, safe=""), json.dumps(key)[1:-1],
            base64.b64encode(key.encode()).decode(), key.encode().hex()]


@pytest.mark.parametrize("raw,within_budget,reason", [
    (b"\xff\xfe", True, "not_utf8"),
    (b"secret body", False, "body_budget_exceeded"),
])
def test_withheld_bodies_still_have_exact_hash_size(raw, within_budget, reason):
    result = diagnostics._body(raw, set(), within_budget=within_budget)
    assert result == {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                      "content_state": "withheld", "reason": reason}


def test_header_redaction_covers_credentials_and_duplicates():
    headers = httpx.Headers([("authorization", "Bearer private"), ("set-cookie", "session=private"),
                            ("X-API-Key", "private"), ("x-token", "private"),
                            ("x-secret", "private"), ("x-request-id", "r1"), ("x-request-id", "r2")])
    clean = diagnostics._headers(headers, set())
    assert all(value == "[REDACTED]" for name, value in clean if name != "x-request-id")
    assert clean[-2:] == [["x-request-id", "r1"], ["x-request-id", "r2"]]


def test_one_shot_concurrent_claim_and_exact_capture(private_arm):
    directory, path, arm, scope = private_arm
    original = path.read_bytes()
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda _: _capture(scope), range(20)))
    files = sorted(item.name for item in directory.iterdir())
    assert files == ["armed.json", f"capture-{arm['nonce']}.json", f"claim-{arm['nonce']}.json"]
    assert path.read_bytes() == original
    capture = directory / f"capture-{arm['nonce']}.json"
    result = json.loads(capture.read_text())
    assert capture.stat().st_mode & 0o777 == 0o600
    assert result["call_id"] == str(scope["call_id"])
    assert result["request"]["body"]["utf8"] == _error().request.content.decode()
    assert result["response"]["body"]["utf8"] == _RESPONSE.decode()
    assert result["response"]["body"]["content_state"] == "exact_utf8"
    assert _KEY not in capture.read_text()


@pytest.mark.parametrize("change", ["wrong-project", "expired", "over-hour", "naive-time", "bad-version", "bad-nonce", "oversize", "extra-field"])
def test_invalid_arm_produces_no_claim(private_arm, change):
    directory, path, arm, scope = private_arm
    if change == "wrong-project":
        arm["project_id"] = str(uuid4())
    elif change == "expired":
        arm["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif change == "over-hour":
        arm["expires_at"] = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    elif change == "naive-time":
        arm["expires_at"] = "2099-01-01T00:00:00"
    elif change == "bad-version":
        arm["version"] = True
    elif change == "bad-nonce":
        arm["nonce"] = "../../outside"
    elif change == "extra-field":
        arm["secret"] = "not-allowed"
    path.write_text("x" * 1025 if change == "oversize" else json.dumps(arm))
    _capture(scope)
    assert list(directory.iterdir()) == [path]


@pytest.mark.parametrize("unsafe", ["directory-mode", "arm-mode", "arm-symlink", "directory-symlink", "arm-hardlink"])
def test_unsafe_filesystem_fails_closed(private_arm, unsafe):
    directory, path, arm, scope = private_arm
    outside = directory.parent / "outside"
    if unsafe == "directory-mode":
        directory.chmod(0o755)
    elif unsafe == "arm-mode":
        path.chmod(0o644)
    elif unsafe == "arm-symlink":
        path.rename(outside)
        path.symlink_to(outside)
    elif unsafe == "directory-symlink":
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
    else:
        os.link(path, outside)
    _capture(scope)
    assert not list(directory.glob("claim-*")) and not list(directory.glob("capture-*"))


def test_sink_failure_retains_claim_without_touching_arm(private_arm, monkeypatch):
    directory, path, arm, scope = private_arm
    original, write = path.read_bytes(), diagnostics._write_new

    def fail_after_claim(fd, name, data):
        if name.startswith("pending-"):
            raise OSError("synthetic private disk failure")
        write(fd, name, data)

    monkeypatch.setattr(diagnostics, "_write_new", fail_after_claim)
    _capture(scope)
    assert (directory / f"claim-{arm['nonce']}.json").exists()
    assert path.read_bytes() == original and not list(directory.glob("capture-*"))
    monkeypatch.setattr(diagnostics, "_write_new", write)
    _capture(scope)
    assert not list(directory.glob("capture-*"))


@pytest.mark.parametrize("name", ["claim", "capture", "pending"])
def test_preexisting_symlink_output_is_never_followed_or_overwritten(private_arm, name):
    directory, path, arm, scope = private_arm
    outside = directory.parent / "untouched"
    outside.write_text("keep-this-exactly")
    output = directory / f"{name}-{arm['nonce']}.json"
    output.symlink_to(outside)
    original = path.read_bytes()
    _capture(scope)
    assert outside.read_text() == "keep-this-exactly"
    assert output.is_symlink() and path.read_bytes() == original


def test_wrong_owner_fails_closed(private_arm, monkeypatch):
    directory, path, arm, scope = private_arm
    owner = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)
    _capture(scope)
    assert list(directory.iterdir()) == [path]


def test_capture_total_body_limit_is_explicit(private_arm):
    directory, path, arm, scope = private_arm
    _capture(scope, _error(body=b"a" * diagnostics._MAX_BODY_BYTES))
    result = json.loads((directory / f"capture-{arm['nonce']}.json").read_text())
    assert result["request"]["body"]["reason"] == "body_budget_exceeded"
    assert result["response"]["body"]["reason"] == "body_budget_exceeded"
    assert "utf8" not in result["request"]["body"]
