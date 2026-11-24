"""Process-local registry default for a Project Machine's frozen starter install.

Also executed inside the guest using only its Python standard library.
"""

from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path

FROZEN_INSTALL = ["pnpm", "install", "--frozen-lockfile"]


def frozen_install_command(argv: list[str], proxy_ip: object) -> list[str]:
    """Only a controller-recorded Cell proxy can supply the local registry."""
    if argv != FROZEN_INSTALL or not isinstance(proxy_ip, str):
        return argv
    try:
        address = ipaddress.IPv4Address(proxy_ip)
    except ValueError:
        return argv
    if (not address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_unspecified):
        return argv
    script = Path(__file__).read_text(encoding="utf-8")
    return ["python3", "-I", "-S", "-c", script, str(address)]


def install_environment(proxy_ip: str, cwd: Path, env: dict[str, str]) -> dict[str, str]:
    # Explicit package manager configuration (including scoped/private registry
    # credentials) stays under pnpm's control. Do not read or transfer its contents.
    normalized = {key.lower(): value for key, value in env.items()}
    if {"npm_config_userconfig", "npm_config_globalconfig", "destdir", "npm_token",
        "node_auth_token"}.intersection(normalized) or any(
        key.startswith("npm_config_") and ("registry" in key or "auth" in key) for key in normalized
    ):
        return env
    home = Path(env.get("HOME", "/root"))
    config_home = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    configs = [
        *(directory / ".npmrc" for directory in (cwd, *cwd.parents)),
        home / ".npmrc", config_home / "pnpm" / "rc",
        Path("/etc/npmrc"), Path("/usr/local/etc/npmrc"),
    ]
    for key in ("prefix", "npm_config_prefix"):
        if prefix := normalized.get(key):
            configs.append(Path(prefix) / "etc/npmrc")
    if any(path.exists() for path in configs):
        return env
    # Keep other destinations behind the existing egress proxy. The local
    # registry itself expects origin-form HTTP directly on this same sidecar.
    bypass = ",".join(dict.fromkeys(
        entry.strip() for raw in (env.get("no_proxy", ""), env.get("NO_PROXY", ""), proxy_ip)
        for entry in raw.split(",") if entry.strip()
    ))
    return {
        **env,
        "npm_config_registry": f"http://{proxy_ip}:3128/__yleum/npm/",
        "no_proxy": bypass,
        "NO_PROXY": bypass,
    }


if __name__ == "__main__":
    environment = install_environment(sys.argv[1], Path.cwd(), dict(os.environ))
    os.execvpe("pnpm", FROZEN_INSTALL, environment)
