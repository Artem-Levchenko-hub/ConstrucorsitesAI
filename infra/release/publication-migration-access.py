#!/usr/bin/env python3
"""Explicit, create-only PV read grant after the canonical production release."""
import argparse
import json
import re
import subprocess
from pathlib import Path
from uuid import UUID

NAME = "omnia-orchestrator-publication-pv-reader"
LABELS = {"app.kubernetes.io/managed-by": "yleum-release",
          "yleum.ru/purpose": "publication-migration-pv-read"}
OBJECTS = (
    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
     "metadata": {"name": NAME, "labels": LABELS},
     "rules": [{"apiGroups": [""], "resources": ["persistentvolumes"],
                "verbs": ["get"]}]},
    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
     "metadata": {"name": NAME, "labels": LABELS},
     "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                 "name": NAME},
     "subjects": [{"kind": "ServiceAccount", "name": "omnia-orchestrator",
                   "namespace": "max-system"}]},
)


class AccessError(Exception):
    """Only static codes may cross the CLI boundary."""


def command(argv, *, payload=None):
    try:
        result = subprocess.run(argv, input=payload, capture_output=True, text=True,
                                timeout=12, check=False)
    except (OSError, subprocess.SubprocessError):
        raise AccessError("command_outcome_unknown") from None
    if result.returncode:
        raise AccessError("command_failed") from None
    if len(result.stdout) > 262144:
        raise AccessError("response_too_large")
    return result.stdout


def kubectl(*args, payload=None):
    return command(["kubectl", "--context", "max-runtime", "--request-timeout=8s",
                    *args], payload=payload)


def read_object(kind, name=NAME, *, namespace=None):
    args = ["get", kind, name, "--ignore-not-found", "-o", "json"]
    if namespace:
        args += ["--namespace", namespace]
    raw = kubectl(*args)
    if not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        raise AccessError("invalid_response") from None
    if not isinstance(value, dict):
        raise AccessError("invalid_response")
    return value


def identity(value, kind, name, namespace=None):
    metadata = value.get("metadata", {})
    if (not isinstance(metadata, dict) or value.get("kind") != kind
            or metadata.get("name") != name or metadata.get("namespace") != namespace
            or metadata.get("deletionTimestamp") is not None):
        raise AccessError("identity_mismatch")
    try:
        uid = metadata["uid"]
        if not isinstance(uid, str) or str(UUID(uid)) != uid:
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AccessError("invalid_uid") from None
    return uid


def validate(value, expected):
    uid = identity(value, expected["kind"], NAME)
    # Reject aggregationRule and future grant-bearing fields, not merely known drift.
    if set(value) != set(expected):
        raise AccessError("grant_drift")
    allowed_metadata = {"name", "labels", "uid", "resourceVersion",
                        "creationTimestamp", "managedFields"}
    metadata = value["metadata"]
    if (set(metadata) - allowed_metadata or metadata.get("labels") != LABELS
            or any(value[key] != item for key, item in expected.items()
                   if key != "metadata")):
        raise AccessError("grant_drift")
    return uid


def execute(expected_revision, apply):
    root = Path(__file__).resolve().parents[2]
    if Path.cwd().resolve() != root or not re.fullmatch(r"[0-9a-f]{40}", expected_revision):
        raise AccessError("revision_guard_failed")
    if command(["git", "rev-parse", "HEAD"]).strip() != expected_revision:
        raise AccessError("revision_guard_failed")
    # Read every precondition before the first write, including a pre-existing binding.
    service_account = read_object("ServiceAccount", "omnia-orchestrator", namespace="max-system")
    observed = [read_object(obj["kind"]) for obj in OBJECTS]
    if service_account is None:
        raise AccessError("service_account_missing")
    identity(service_account, "ServiceAccount", "omnia-orchestrator", "max-system")
    uids = {}
    for value, expected in zip(observed, OBJECTS, strict=True):
        if value is not None:
            uids[expected["kind"]] = validate(value, expected)
    if not apply and any(value is None for value in observed):
        raise AccessError("grant_absent")
    for value, expected in zip(observed, OBJECTS, strict=True):
        if value is None:
            # An ambiguous acknowledgement or concurrent create stops; never retry/adopt.
            kubectl("create", "-f", "-", payload=json.dumps(expected))
            value = read_object(expected["kind"])
            if value is None:
                raise AccessError("readback_missing")
            uids[expected["kind"]] = validate(value, expected)
    # Re-read both objects: reject drift or UID replacement during this operation.
    for expected in OBJECTS:
        value = read_object(expected["kind"])
        if value is None:
            raise AccessError("readback_missing")
        if validate(value, expected) != uids[expected["kind"]]:
            raise AccessError("uid_changed")
    return {"status": "verified", "mode": "apply" if apply else "check"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-revision", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true", help="read-only default")
    args = parser.parse_args(argv)
    try:
        result = execute(args.expected_revision, args.apply)
    except AccessError as exc:
        print(json.dumps({"status": "blocked", "error_code": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
