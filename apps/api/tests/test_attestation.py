"""Build-attestation assembly — pure, no DB/browser."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from omnia_api.services.attestation import (
    build_attestation,
    to_log_line,
    verify_digest,
)


@dataclass
class _Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class _Verdict:
    # Structural stand-in for FunctionalVerdict / SecurityVerdict.
    passed: bool
    checks: list = field(default_factory=list)


_TS = "2026-07-05T18:00:00+00:00"


def _att(gates, **kw):
    return build_attestation(
        gates=gates, stack="nextjs-postgres-drizzle", project_id="p-1", created_at=_TS, **kw
    )


def test_all_gates_pass_is_proven() -> None:
    att = _att(
        [
            ("isolation", _Verdict(True, [_Check("anon-leak", True)])),
            ("security", _Verdict(True, [_Check("nosniff", True)])),
        ]
    )
    assert att["overall_passed"] is True
    assert {g["name"] for g in att["gates"]} == {"isolation", "security"}
    assert att["version"] == 1
    assert verify_digest(att)


def test_one_gate_fails_blocks_overall() -> None:
    att = _att(
        [
            ("isolation", _Verdict(False, [_Check("anon-leak", False, "collection served to anon")])),
            ("security", _Verdict(True, [_Check("nosniff", True)])),
        ]
    )
    assert att["overall_passed"] is False


def test_empty_gates_is_not_proven() -> None:
    # No gate ran → NOT "proven safe"; explicitly not-proven.
    att = _att([])
    assert att["overall_passed"] is False
    assert att["gates"] == []


def test_none_verdicts_are_skipped() -> None:
    att = _att([("isolation", _Verdict(True, [_Check("x", True)])), ("security", None)])
    assert [g["name"] for g in att["gates"]] == ["isolation"]


def test_digest_is_tamper_evident() -> None:
    att = _att([("isolation", _Verdict(True, [_Check("anon-leak", True)]))])
    assert verify_digest(att)
    # Flip a recorded check result → digest must no longer verify.
    att["gates"][0]["checks"][0]["ok"] = False
    assert not verify_digest(att)


def test_digest_is_deterministic() -> None:
    a = _att([("isolation", _Verdict(True, [_Check("a", True)]))])
    b = _att([("isolation", _Verdict(True, [_Check("a", True)]))])
    assert a["digest"] == b["digest"]


def test_commit_sha_is_carried() -> None:
    att = _att([("isolation", _Verdict(True, [_Check("x", True)]))], commit_sha="deadbeef")
    assert att["commit_sha"] == "deadbeef"
    assert verify_digest(att)


def test_to_log_line_roundtrips() -> None:
    att = _att([("isolation", _Verdict(True, [_Check("x", True)]))])
    parsed = json.loads(to_log_line(att))
    assert parsed["digest"] == att["digest"]
    assert verify_digest(parsed)
