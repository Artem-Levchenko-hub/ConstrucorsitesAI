"""Portable AES-256-GCM profiles, independent of any local Windows DPAPI key.

Private values are explicitly serialized only at this encryption boundary.
There is no plaintext export API or plaintext profile persistence.
"""

import base64
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .contracts import RecoveryProfile
from .redaction import ProfileInvalid

_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_HEADER_FIELDS = {"format_version", "algorithm", "profile_id", "nonce"}


def _validate_name(name: str) -> None:
    if not _NAME.fullmatch(name) or name in _RESERVED:
        raise ProfileInvalid()


def _check_path(path: Path) -> None:
    """Refuse relative paths, traversal, and every existing reparse-point ancestor."""
    if not path.is_absolute() or ".." in path.parts:
        raise ProfileInvalid()
    if os.name == "nt" and (
        path.drive.startswith("\\\\")
        or any(":" in part or part.endswith((".", " ")) for part in path.parts[1:])
    ):
        # No UNC/device paths, alternate data streams, or DOS path aliases.
        raise ProfileInvalid()
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ProfileInvalid()
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ProfileInvalid()


def resolve_profile(name: str, home: Path) -> Path:
    try:
        _validate_name(name)
        path = home / f"{name}.yleum-recovery"
        _check_path(path)
        if home.exists() and not home.is_dir():
            raise ProfileInvalid()
        return path
    except (OSError, ValueError, TypeError):
        raise ProfileInvalid() from None


def _json(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _no_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileInvalid()
        result[key] = value
    return result


def seal_profile(profile: RecoveryProfile, key: bytes) -> bytes:
    try:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ProfileInvalid()
        _validate_name(profile.name)
        nonce = os.urandom(12)
        header = {
            "format_version": 1,
            "algorithm": "AES-256-GCM",
            "profile_id": profile.name,
            "nonce": base64.b64encode(nonce).decode("ascii"),
        }
        # Ordinary model dumps exclude these fields deliberately. Unwrap them here only.
        data = profile.model_dump(mode="json", exclude={"secret_export_id"})
        material = profile.secret_material
        data["secret_material"] = {
            "format_version": material.format_version,
            "export_id": material.export_id,
            "credentials": {k: v.get_secret_value() for k, v in material.credentials.items()},
            "keys": {k: v.get_secret_value() for k, v in material.keys.items()},
            "role_exports": {
                role.value: {k: v.get_secret_value() for k, v in values.items()}
                for role, values in material.role_exports.items()
            },
        }
        encrypted = AESGCM(key).encrypt(nonce, _json(data), _json(header))
        return _json(header | {"ciphertext": base64.b64encode(encrypted).decode("ascii")})
    except Exception:
        raise ProfileInvalid() from None


def open_profile(ciphertext: bytes, key: bytes) -> RecoveryProfile:
    try:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ProfileInvalid()
        envelope = json.loads(ciphertext, object_pairs_hook=_no_duplicate_fields)
        if not isinstance(envelope, dict) or set(envelope) != _HEADER_FIELDS | {"ciphertext"}:
            raise ProfileInvalid()
        if type(envelope["format_version"]) is not int or envelope["format_version"] != 1:
            raise ProfileInvalid()
        if envelope["algorithm"] != "AES-256-GCM":
            raise ProfileInvalid()
        _validate_name(envelope["profile_id"])
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        if len(nonce) != 12:
            raise ProfileInvalid()
        encrypted = base64.b64decode(envelope["ciphertext"], validate=True)
        header = {field: envelope[field] for field in _HEADER_FIELDS}
        plaintext = AESGCM(key).decrypt(nonce, encrypted, _json(header))
        data = json.loads(plaintext, object_pairs_hook=_no_duplicate_fields)
        profile = RecoveryProfile.model_validate(data)
        if profile.name != envelope["profile_id"]:
            raise ProfileInvalid()
        return profile
    except Exception:
        # ValidationError/InvalidTag may carry private input. Expose only the stable code.
        raise ProfileInvalid() from None
