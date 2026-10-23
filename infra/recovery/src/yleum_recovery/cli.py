"""Local manifest verification and explicit secure profile activation."""

import argparse
import base64
import getpass
import json
import os
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from .contracts import BackupManifest, Failure, Proof, ReleaseManifest
from .local_unlock import activate_local_key, load_local_key
from .manifest import verify_manifests
from .profile import open_profile, resolve_profile
from .redaction import LocalUnlockError, ProfileInvalid, RecoveryError


def _read_recovery_key() -> bytes:
    if not sys.stdin.isatty():
        raise LocalUnlockError()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass("Recovery key (base64, hidden input): ")
        key = base64.b64decode(value, validate=True)
        if len(key) != 32 or base64.b64encode(key).decode("ascii") != value:
            raise LocalUnlockError()
        return key
    except (Exception, KeyboardInterrupt):
        raise LocalUnlockError() from None


def _profile_command(action: str, name: str) -> int:
    try:
        if action == "activate":
            key = _read_recovery_key()
        else:
            key = load_local_key(name)
        home = os.environ.get("YLEUM_RECOVERY_HOME")
        if not home:
            raise LocalUnlockError()
        try:
            profile = open_profile(resolve_profile(name, Path(home)).read_bytes(), key)
        except OSError:
            raise ProfileInvalid() from None
        if profile.name != name:
            raise ProfileInvalid()
        if action == "activate":
            activate_local_key(name, key)
        print(
            json.dumps(
                {
                    "status": "profile-activated" if action == "activate" else "profile-verified",
                    "profile": profile.public_reference,
                    "secret_export_id": profile.secret_export_id,
                }
            )
        )
        return 0
    except RecoveryError as error:
        proof = Proof(ok=False, failures=(Failure(code=error.code, message=error.message),))
        print(json.dumps({"status": "profile-unavailable", "proof": proof.model_dump(mode="json")}))
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yleum")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="Verify local recovery metadata compatibility")
    verify.add_argument("--release", type=Path, required=True)
    verify.add_argument("--backup", type=Path, required=True)
    profile = subparsers.add_parser("profile", help="Activate or verify an encrypted local profile")
    actions = profile.add_subparsers(dest="profile_action", required=True)
    for action in ("activate", "verify"):
        command = actions.add_parser(action)
        command.add_argument("--name", default="cortel")
    args = parser.parse_args(argv)
    if args.command == "profile":
        return _profile_command(args.profile_action, args.name)
    try:
        release = ReleaseManifest.model_validate_json(args.release.read_bytes())
        backup = BackupManifest.model_validate_json(args.backup.read_bytes())
    except (OSError, ValidationError):
        # Pydantic diagnostics and paths may contain supplied secret values.
        proof = Proof(
            ok=False,
            failures=(
                Failure(
                    code="INVALID_MANIFEST",
                    message="Manifest could not be read or validated",
                ),
            ),
        )
        print(json.dumps({"status": "invalid-manifest", "proof": proof.model_dump(mode="json")}))
        return 2
    proof = verify_manifests(release, backup)
    status = "metadata-compatible" if proof.ok else "metadata-incompatible"
    print(json.dumps({"status": status, "proof": proof.model_dump(mode="json")}))
    return 0 if proof.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
