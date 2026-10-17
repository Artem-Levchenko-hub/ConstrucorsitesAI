"""Offline campaign defaults and real owner-API admission boundaries (fake transport)."""

import importlib.util
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest


def campaign():
    path = Path(__file__).resolve().parents[3] / "tools/qa/generation_recovery_campaign.py"
    assert path.is_file(), "missing default-readonly campaign runner"
    spec = importlib.util.spec_from_file_location("campaign", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_is_offline_and_never_changes_checkpoint(tmp_path, monkeypatch):
    c = campaign()
    ids = (uuid4(), uuid4())
    path = tmp_path / "checkpoint.json"
    raw = {
        "version": 1,
        "release_sha": "a" * 40,
        "project_ids": [str(x) for x in ids],
        "attempts": [],
        "requests": [],
        "accepted_rounds": 0,
    }
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    monkeypatch.setattr(c, "OwnerAPI", lambda *a, **k: pytest.fail("offline opened transport"))
    result = c.run_campaign(checkpoint_path=path, expected_release="a" * 40, project_ids=ids)
    assert result["mode"] == "read_only" and result["new_generations"] == 0
    assert result["live_acceptance"] == "NOT_RUN"
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["revision", "same_project", "wrong_project", "unknown_cap"])
def test_wrong_scope_refuses_before_transport(tmp_path, monkeypatch, fault):
    c = campaign()
    ids = (uuid4(), uuid4())
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "release_sha": "a" * 40,
                "project_ids": [str(x) for x in ids],
                "attempts": [],
                "requests": [],
                "accepted_rounds": 0,
            }
        )
    )
    monkeypatch.setattr(c, "OwnerAPI", lambda *a, **k: pytest.fail("scope opened transport"))
    with pytest.raises(c.CampaignRefused):
        c.run_campaign(
            checkpoint_path=path,
            expected_release="b" * 40 if fault == "revision" else "a" * 40,
            project_ids=(ids[0], ids[0])
            if fault == "same_project"
            else (uuid4(), ids[1])
            if fault == "wrong_project"
            else ids,
            execute=True,
        )


def _executable(tmp_path):
    import hashlib
    import time
    from datetime import UTC, datetime, timedelta

    ids, owner = (uuid4(), uuid4()), uuid4()
    revision = "a" * 40
    receipt = tmp_path / "deployment.json"
    receipt.write_text(
        json.dumps(
            {
                "observed_at": int(time.time()),
                "services": {
                    key: revision
                    for key in (
                        "api",
                        "web",
                        "gateway",
                        "worker",
                        "generation_worker",
                        "orchestrator_core",
                        "orchestrator_commerce",
                    )
                },
            }
        )
    )
    now = datetime.now(UTC)
    manifests = [
        {
            "version": 1,
            "run_id": str(uuid4()),
            "release_sha": revision,
            "mode": "production_disposable",
            "project_id": str(project),
            "owner_id": str(owner),
            "workspace_id": str(uuid4()),
            "fixture_digest": "b" * 64,
            "database_identity_digest": "c" * 64,
            "resource_manifest_digest": "d" * 64,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }
        for project in ids
    ]
    requests = []
    for alias in ("A", "B"):
        prompt = tmp_path / f"{alias}.txt"
        prompt.write_text("synthetic request", encoding="utf-8")
        requests.append(
            {
                "project": alias,
                "prompt_file": str(prompt),
                "state": "planned",
                "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                "idempotency_key": str(uuid4()),
            }
        )
    state = {
        "version": 1,
        "release_sha": revision,
        "project_ids": [str(x) for x in ids],
        "owner_id": str(owner),
        "origin": "https://qa.example.invalid",
        "max_admissions": 2,
        "qa_manifests": manifests,
        "bot_a_sha256": hashlib.sha256(b"synthetic-bot").hexdigest(),
        "deployment_evidence": {
            "path": str(receipt),
            "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
        },
        "requests": requests,
        "attempts": [],
        "accepted_rounds": 0,
    }
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path, state, ids


@pytest.mark.parametrize("fault", [None, "owner", "release", "bot", "unknown_post"])
def test_actual_campaign_only_normal_owner_api_and_durable_intents(tmp_path, monkeypatch, fault):
    import threading

    c = campaign()
    path, state, ids = _executable(tmp_path)
    posts = []
    barrier = threading.Barrier(2)

    class API:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, route, payload=None):
            if method == "POST":
                saved = json.loads(path.read_text())
                assert len(saved["attempts"]) == 2
                assert all(row["state"] == "outcome_unknown" for row in saved["attempts"])
                assert route.endswith("/prompt")
                assert set(payload) == {"prompt", "idempotency_key"}
                posts.append(route)
                barrier.wait(timeout=3)
                if fault == "unknown_post":
                    raise OSError("synthetic private error must never escape")
                return {"run_id": str(uuid4()), "replayed": False}
            if route == "/api/health":
                return {"release_sha": "b" * 40 if fault == "release" else "a" * 40}
            if route == "/api/auth/me":
                return {"id": str(uuid4()) if fault == "owner" else state["owner_id"]}
            if route.endswith("/integrations/max"):
                first = str(ids[0]) in route
                return {
                    "connected": first,
                    "bot_id": ("foreign" if fault == "bot" else "synthetic-bot") if first else None,
                }
            return {"id": route.rsplit("/", 1)[-1], "owner_id": state["owner_id"]}

    monkeypatch.setenv("GENERATION_RECOVERY_QA_PROFILE", str(tmp_path / "private-profile.json"))
    monkeypatch.setattr(c, "OwnerAPI", API)
    if fault in {"owner", "release", "bot"}:
        before = path.read_bytes()
        with pytest.raises(c.CampaignRefused):
            c.run_campaign(
                checkpoint_path=path, expected_release="a" * 40, project_ids=ids, execute=True
            )
        assert posts == [] and path.read_bytes() == before
    else:
        result = c.run_campaign(
            checkpoint_path=path, expected_release="a" * 40, project_ids=ids, execute=True
        )
        assert len(posts) == 2
        assert result["live_acceptance"] == "NOT_RUN" and result["accepted_rounds"] == 0
        saved = path.read_text()
        assert "synthetic request" not in saved and "private error" not in saved
        if fault == "unknown_post":
            assert result["new_generations"] is None and result["unknown_admissions"] == 2
            with pytest.raises(c.CampaignRefused, match="reconcile_unknown"):
                c.run_campaign(
                    checkpoint_path=path, expected_release="a" * 40, project_ids=ids, execute=True
                )
            assert len(posts) == 2


@pytest.mark.parametrize(
    "fault", ["domain", "path", "secure", "expired", "duplicate", "origin", "infinite"]
)
def test_private_profile_does_not_forward_broad_or_expired_cookie(tmp_path, fault):
    import time

    c = campaign()
    owner = str(uuid4())
    cookie = {
        "name": "omnia_session",
        "value": "synthetic-private-cookie",
        "domain": "qa.example.invalid",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "expires": time.time() + 3600,
    }
    if fault == "domain":
        cookie["domain"] = ".example.invalid"
    elif fault == "path":
        cookie["path"] = "/foreign"
    elif fault == "secure":
        cookie["secure"] = False
    elif fault == "expired":
        cookie["expires"] = -1
    elif fault == "infinite":
        cookie["expires"] = float("inf")
    storage = tmp_path / "storage.json"
    storage.write_text(
        json.dumps({"cookies": [cookie, cookie] if fault == "duplicate" else [cookie]})
    )
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "origin": "https://foreign.invalid"
                if fault == "origin"
                else "https://qa.example.invalid",
                "owner_id": owner,
                "storage_state": str(storage),
            }
        )
    )
    with pytest.raises(c.CampaignRefused) as error:
        c.OwnerAPI(profile, expected_origin="https://qa.example.invalid", owner_id=UUID(owner))
    assert "synthetic-private-cookie" not in str(error.value)


def test_owner_transport_has_bounded_safe_outcomes_and_rechecks_expiry(tmp_path, monkeypatch):
    import time

    c = campaign()
    owner = uuid4()
    storage = tmp_path / "storage.json"
    storage.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "omnia_session",
                        "value": "private",
                        "domain": "qa.example.invalid",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                        "expires": time.time() + 60,
                    }
                ]
            }
        )
    )
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "origin": "https://qa.example.invalid",
                "owner_id": str(owner),
                "storage_state": str(storage),
            }
        )
    )
    api = c.OwnerAPI(profile, expected_origin="https://qa.example.invalid", owner_id=owner)
    monkeypatch.setattr(api, "_request", lambda *a: {"ok": True})
    assert api.request("GET", "/api/health") == {"ok": True}

    def private_failure(*args):
        raise ValueError("private-cookie-value-must-not-escape")

    monkeypatch.setattr(api, "_request", private_failure)
    with pytest.raises(c.CampaignRefused, match=r"^owner_api_outcome_unknown$"):
        api.request("POST", "/api/projects/synthetic/prompt", {})
    api._expires = 0
    monkeypatch.setattr(api, "_request", lambda *a: pytest.fail("expired cookie sent"))
    with pytest.raises(c.CampaignRefused, match="expired"):
        api.request("GET", "/api/health")
