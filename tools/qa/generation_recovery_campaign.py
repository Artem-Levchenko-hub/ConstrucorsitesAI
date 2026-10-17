"""Bounded owner-API admission and an honest, offline-by-default campaign ledger.

No dispatcher, SQL writes, bot binding changes, browser credential export, or
automatic retries. An admission is never a functional acceptance receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import re
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

MAX_JSON = 2 * 1024 * 1024


class CampaignRefused(RuntimeError):
    """Fixed reason only. Transport bodies, cookies and prompts must never escape."""


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise CampaignRefused("duplicate_json_key")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON:
        raise CampaignRefused("invalid_private_artifact")
    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=_pairs)
    except (ValueError, OSError):
        raise CampaignRefused("invalid_json_artifact") from None
    if type(value) is not dict:
        raise CampaignRefused("invalid_json_artifact")
    return value


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise CampaignRefused("invalid_identity")
    try:
        result = UUID(value)
    except ValueError:
        raise CampaignRefused("invalid_identity") from None
    if str(result) != value:
        raise CampaignRefused("noncanonical_identity")
    return result


def _save(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".next")
    # Caller owns the exclusive sidecar for the entire admission batch. A leftover
    # temporary artifact is evidence of an interrupted write, never overwritten.
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OwnerAPI:
    """A separately prepared private profile; never extract the user's live browser."""

    def __init__(self, profile_path: Path, *, expected_origin: str, owner_id: UUID):
        profile = _read(profile_path)
        parsed = urlsplit(expected_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or profile.get("origin") != expected_origin
            or profile.get("owner_id") != str(owner_id)
        ):
            raise CampaignRefused("private_profile_scope")
        storage = _read(Path(profile.get("storage_state", "")))
        cookies = storage.get("cookies")
        if not isinstance(cookies, list):
            raise CampaignRefused("private_profile_cookie")
        allowed = [
            cookie
            for cookie in cookies
            if isinstance(cookie, dict)
            and cookie.get("name") == "omnia_session"
            and cookie.get("domain") == parsed.hostname
            and cookie.get("path") in {"/", "/api"}
            and cookie.get("secure") is True
            and cookie.get("httpOnly") is True
            and type(cookie.get("expires")) in {int, float}
            and math.isfinite(cookie["expires"])
            and cookie["expires"] > time.time()
        ]
        if len(allowed) != 1 or not isinstance(allowed[0].get("value"), str):
            raise CampaignRefused("private_profile_cookie")
        value = allowed[0]["value"]
        if not value or any(ord(char) < 33 or char in ";," for char in value):
            raise CampaignRefused("private_profile_cookie")
        self.origin, self._cookie = expected_origin, "omnia_session=" + value
        self._expires = allowed[0]["expires"]

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if time.time() >= self._expires:
            raise CampaignRefused("private_profile_expired")
        results: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

        def bounded_request() -> None:
            try:
                results.put(self._request(method, path, payload))
            except Exception:
                results.put(None)

        # Same bounded daemon pattern as production_smoke: DNS/slow-drip response
        # cannot pin the operator indefinitely. A timed-out POST remains unknown.
        threading.Thread(target=bounded_request, daemon=True).start()
        try:
            result = results.get(timeout=35)
        except queue.Empty:
            raise CampaignRefused("owner_api_outcome_unknown") from None
        if result is None:
            raise CampaignRefused("owner_api_outcome_unknown")
        return result

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if method not in {"GET", "POST"} or not path.startswith("/api/") or "?" in path:
            raise CampaignRefused("invalid_api_route")
        try:
            request = Request(
                self.origin + path,
                method=method,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Cookie": self._cookie, "Content-Type": "application/json"},
            )
            with build_opener(_NoRedirect()).open(request, timeout=30) as response:
                if response.status not in ({202} if method == "POST" else {200}):
                    raise CampaignRefused("unexpected_api_status")
                raw = response.read(MAX_JSON + 1)
                if len(raw) > MAX_JSON:
                    raise CampaignRefused("oversized_api_response")
                result = json.loads(raw, object_pairs_hook=_pairs)
            if not isinstance(result, dict):
                raise CampaignRefused("invalid_api_response")
            return result
        except HTTPError:
            raise CampaignRefused("owner_api_refused") from None
        except Exception:
            raise CampaignRefused("owner_api_outcome_unknown") from None


def _scope(api: OwnerAPI, state: dict[str, Any], ids: tuple[UUID, UUID]) -> None:
    if api.request("GET", "/api/health").get("release_sha") != state["release_sha"]:
        raise CampaignRefused("release_mismatch")
    if api.request("GET", "/api/auth/me").get("id") != state["owner_id"]:
        raise CampaignRefused("owner_mismatch")
    for index, project_id in enumerate(ids):
        project = api.request("GET", f"/api/projects/{project_id}")
        if (
            project.get("id") != str(project_id)
            or project.get("owner_id") != state["owner_id"]
        ):
            raise CampaignRefused("project_owner_mismatch")
        bot = api.request("GET", f"/api/projects/{project_id}/integrations/max")
        if index == 0:
            actual = bot.get("bot_id")
            if (
                bot.get("connected") is not True
                or not isinstance(actual, str)
                or hashlib.sha256(actual.encode()).hexdigest() != state["bot_a_sha256"]
            ):
                raise CampaignRefused("bot_binding_mismatch")
        elif bot.get("connected") is not False or bot.get("bot_id") is not None:
            raise CampaignRefused("bot_b_must_be_absent")


def _execution_inputs(
    state: dict[str, Any], ids: tuple[UUID, UUID]
) -> list[tuple[int, str]]:
    from yleum_api.ops.production_smoke import validate_recovery_campaign_identity
    from yleum_api.ops.restoration_qa.contracts import QaManifest

    owner = _uuid(state.get("owner_id"))
    cap = state.get("max_admissions")
    if type(cap) is not int or not 1 <= cap <= 100:
        raise CampaignRefused("explicit_admission_cap_required")
    proof = state.get("deployment_evidence")
    if type(proof) is not dict or set(proof) != {"path", "sha256"}:
        raise CampaignRefused("deployment_evidence_required")
    proof_path = Path(proof["path"])
    receipt = _read(proof_path)
    if (
        hashlib.sha256(proof_path.read_bytes()).hexdigest() != proof["sha256"]
        or not validate_recovery_campaign_identity(
            state["release_sha"], receipt.get("services", {})
        )
        or type(receipt.get("observed_at")) is not int
        or not 0 <= time.time() - receipt["observed_at"] <= 300
    ):
        raise CampaignRefused("deployment_evidence_stale_or_mismatched")
    manifests = state.get("qa_manifests")
    if not isinstance(manifests, list) or len(manifests) != 2:
        raise CampaignRefused("isolated_ownership_evidence_required")
    for project_id, raw in zip(ids, manifests, strict=True):
        try:
            manifest = QaManifest.model_validate(raw)
        except ValueError:
            raise CampaignRefused("invalid_qa_manifest") from None
        if (
            manifest.project_id != project_id
            or manifest.owner_id != owner
            or manifest.release_sha != state["release_sha"]
            or manifest.mode != "production_disposable"
            or manifest.expires_at.tzinfo is None
            or manifest.created_at.tzinfo is None
            or not manifest.created_at <= datetime.now(UTC) < manifest.expires_at
        ):
            raise CampaignRefused("stale_or_foreign_qa_manifest")
    if any(row.get("state") == "outcome_unknown" for row in state["attempts"]):
        raise CampaignRefused("reconcile_unknown_admission_first")
    selected = [
        (i, row)
        for i, row in enumerate(state["requests"])
        if row.get("state") == "planned"
    ][:2]
    if not selected or len(state["attempts"]) + len(selected) > cap:
        raise CampaignRefused("admission_cap_or_no_planned_request")
    aliases = [row.get("project") for _, row in selected]
    if any(alias not in {"A", "B"} for alias in aliases) or len(set(aliases)) != len(
        aliases
    ):
        raise CampaignRefused("one_request_per_isolated_project")
    result = []
    keys = [row.get("idempotency_key") for row in state["requests"]]
    if len(set(keys)) != len(keys):
        raise CampaignRefused("duplicate_request_identity")
    for index, request in selected:
        _uuid(request.get("idempotency_key"))
        path = Path(request.get("prompt_file", ""))
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 120_000:
            raise CampaignRefused("private_prompt_unavailable")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != request.get("prompt_sha256"):
            raise CampaignRefused("literal_prompt_digest_mismatch")
        prompt = raw.decode("utf-8")
        if not 1 <= len(prompt) <= 30_000:
            raise CampaignRefused("invalid_literal_prompt")
        result.append((index, prompt))
    return result


def run_campaign(
    *,
    checkpoint_path: Path,
    expected_release: str,
    project_ids: tuple[UUID, UUID],
    execute: bool = False,
) -> dict[str, object]:
    state = _read(checkpoint_path)
    if (
        not re.fullmatch(r"[a-f0-9]{40}", expected_release)
        or type(state.get("version")) is not int
        or state.get("version") != 1
        or state.get("release_sha") != expected_release
        or len(set(project_ids)) != 2
        or state.get("project_ids") != [str(value) for value in project_ids]
        or not isinstance(state.get("attempts"), list)
        or not isinstance(state.get("requests"), list)
    ):
        raise CampaignRefused("checkpoint_scope_mismatch")
    summary: dict[str, object] = {
        "version": 1,
        "release_sha": expected_release,
        "mode": "read_only",
        "new_generations": 0,
        "live_acceptance": "NOT_RUN",
        "recorded_attempts": len(state["attempts"]),
        "accepted_rounds": 0,
        "second_max_identity": "NOT_RUN_INPUT_UNAVAILABLE",
    }
    if not execute:
        return summary
    # Serialize operators, not the two ordinary server admissions. On process death
    # retain this lock: a root operator must reconcile, never blindly retry a POST.
    lock = checkpoint_path.with_name(checkpoint_path.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        raise CampaignRefused("campaign_already_owned_or_interrupted") from None
    try:
        state = _read(checkpoint_path)  # never act on a pre-lock snapshot
        if state.get("release_sha") != expected_release or state.get("project_ids") != [
            str(x) for x in project_ids
        ]:
            raise CampaignRefused("checkpoint_changed_before_lock")
        selected = _execution_inputs(state, project_ids)
        profile = os.environ.get("GENERATION_RECOVERY_QA_PROFILE")
        if not profile:
            raise CampaignRefused("private_owner_profile_unavailable")
        api = OwnerAPI(
            Path(profile),
            expected_origin=state.get("origin", ""),
            owner_id=_uuid(state["owner_id"]),
        )
        _scope(api, state, project_ids)
        for index, _ in selected:
            request = state["requests"][index]
            request["state"] = "outcome_unknown"
            state["attempts"].append(
                {
                    "idempotency_key": request["idempotency_key"],
                    "project": request["project"],
                    "prompt_sha256": request["prompt_sha256"],
                    "state": "outcome_unknown",
                    "run_id": None,
                    "provider_attempt_ids": [],
                    "requirements": [],
                    "data_evidence": None,
                    "cost": "unknown",
                    "source_sha": expected_release,
                    "functional_acceptance": "NOT_RUN",
                }
            )
        _save(
            checkpoint_path, state
        )  # durable before any POST, including both parallel intents
        new_count = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {}
            for index, prompt in selected:
                request = state["requests"][index]
                project_id = project_ids[0 if request["project"] == "A" else 1]
                future = pool.submit(
                    api.request,
                    "POST",
                    f"/api/projects/{project_id}/prompt",
                    {"prompt": prompt, "idempotency_key": request["idempotency_key"]},
                )
                futures[future] = index
            for future in as_completed(futures):
                request = state["requests"][futures[future]]
                attempt = next(
                    row
                    for row in state["attempts"]
                    if row["idempotency_key"] == request["idempotency_key"]
                )
                try:
                    reply = future.result()
                    run_id = str(_uuid(reply.get("run_id")))
                except Exception:
                    attempt["blocker"] = "admission_outcome_unknown"
                else:
                    attempt.update(state="admitted", run_id=run_id)
                    request["state"] = "admitted"
                    if reply.get("replayed") is False:
                        new_count += 1
                _save(checkpoint_path, state)
        unknown = sum(row["state"] == "outcome_unknown" for row in state["attempts"])
        summary.update(
            mode="execute",
            new_generations=None if unknown else new_count,
            confirmed_new_generations=new_count,
            admission_requests=len(selected),
            unknown_admissions=unknown,
            recorded_attempts=len(state["attempts"]),
        )
        return summary
    finally:
        os.close(descriptor)
        lock.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--project-a", type=UUID, required=True)
    parser.add_argument("--project-b", type=UUID, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_campaign(
            checkpoint_path=args.checkpoint,
            expected_release=args.expected_release,
            project_ids=(args.project_a, args.project_b),
            execute=args.execute,
        )
    except CampaignRefused as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2
    except Exception:
        # Malformed private files/encoding must not print their values in a traceback.
        print(json.dumps({"status": "blocked", "reason": "invalid_campaign_input"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
