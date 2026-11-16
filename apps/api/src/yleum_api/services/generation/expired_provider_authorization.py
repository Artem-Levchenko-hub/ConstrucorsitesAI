"""Load the exact parked-run scope after the production release closes its fences."""

import json
import os
import stat

_AUTHORIZATION = "/var/lib/yleum-expired-provider/scope.json"
_MAX_BYTES = 4096


def read_released_expired_provider_scope() -> str | None:
    release = os.environ.get("OMNIA_RELEASE_SHA")
    if not release or not hasattr(os, "O_NOFOLLOW"):
        return None
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        with os.fdopen(os.open(_AUTHORIZATION, flags), "rb") as file:
            info = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= _MAX_BYTES
            ):
                return None
            data = file.read(_MAX_BYTES + 1)
        if len(data) != info.st_size:
            return None
        value = json.loads(data)
        if (
            type(value) is not dict or set(value) != {"release_sha", "scope"}
            or value["release_sha"] != release or type(value["scope"]) is not str
        ):
            return None
        return value["scope"]
    except (OSError, ValueError):
        return None
