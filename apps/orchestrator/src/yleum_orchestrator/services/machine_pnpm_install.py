"""Process-local public registry for development Cell dependency installs.

Also executed inside the guest using only its Python standard library.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shlex
import sys
from pathlib import Path

_INSTALL_COMMAND = re.compile(r"^(\s*)pnpm(?=[ \t]+(?:add|install|i)(?:\s|$))")


def pnpm_install_command(argv: list[str], proxy_ip: object) -> list[str]:
    """Only a controller-recorded Cell proxy can supply the local registry."""
    shell = len(argv) == 3 and argv[:2] == ["sh", "-lc"] and _INSTALL_COMMAND.match(argv[2])
    direct = len(argv) >= 2 and argv[0] == "pnpm" and argv[1] in {"add", "install", "i"}
    if not (direct or shell) or not isinstance(proxy_ip, str):
        return argv
    try:
        address = ipaddress.IPv4Address(proxy_ip)
    except ValueError:
        return argv
    if (not address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_unspecified):
        return argv
    script = Path(__file__).read_text(encoding="utf-8")
    launcher = ["python3", "-I", "-S", "-c", script, str(address)]
    if shell:
        # Replace only the leading executable. Arguments, redirects and following
        # commands are still interpreted exactly once by the original shell.
        return [*argv[:2], shell[1] + shlex.join(launcher)
                + argv[2][shell.end():]]
    return [*launcher, *argv[1:]]


def install_environment(proxy_ip: str, cwd: Path, env: dict[str, str]) -> dict[str, str]:
    # Explicit package manager configuration (including scoped/private registry
    # credentials) stays under pnpm's control. Do not read or transfer its contents.
    normalized = {key.lower().replace("-", "_"): value for key, value in env.items()}
    proxy_keys = {"http_proxy", "https_proxy", "all_proxy"}
    if any(value and value.rstrip("/") != f"http://{proxy_ip}:3128"
           for key, value in env.items() if key.lower() in proxy_keys):
        return env
    custom = {
        "npm_config_userconfig", "npm_config_globalconfig", "destdir", "npm_token",
        "node_auth_token", "npm_config_lockfile_include_tarball_url", "npm_config_dir",
        "npm_config_global", "npm_config_proxy", "npm_config_https_proxy",
    }
    if custom.intersection(normalized) or any(
        key.startswith("npm_config_") and ("registry" in key or "auth" in key)
        for key in normalized
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
    arguments = sys.argv[2:]
    # Unknown options may choose another project's config, credentials or lockfile
    # format. Leave such a command entirely to pnpm.
    public_options = {"-D", "--save-dev", "-P", "--save-prod", "-O", "--save-optional",
                      "-E", "--save-exact", "--frozen-lockfile", "--offline", "--prefer-offline",
                      "--ignore-scripts", "--lockfile-only", "--no-frozen-lockfile"}
    environment = dict(os.environ)
    if all(not arg.startswith("-") or arg in public_options for arg in arguments):
        environment = install_environment(sys.argv[1], Path.cwd(), environment)
    os.execvpe("pnpm", ["pnpm", *arguments], environment)
