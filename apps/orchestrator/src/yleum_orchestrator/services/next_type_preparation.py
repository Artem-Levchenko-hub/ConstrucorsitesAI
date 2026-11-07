"""Controller-owned regeneration of disposable MAX Next route declarations.

The portable machine image includes Python. Its fd-relative, symlink-resistant
stdlib remover lets us invalidate only ``.next/types`` without touching a ready
compilation or product data. The installed, exact starter Next CLI recreates the
route declarations before the normal, unchanged TypeScript checks run.
"""

from yleum_orchestrator.core.project_machine import MachineManifest

NEXT_TYPE_PREPARATION_PHASE = "controller:next-type-preparation:v1"
NEXT_TYPE_PREPARATION_TIMEOUT = 60
_NODE_COMMANDS = frozenset({"pnpm", "npm", "npx", "node"})


def prepares_next_types(manifest: MachineManifest) -> bool:
    """Limit the new controller phase to the root MAX portable Node contract."""
    checks = [task for task in manifest.tasks if task.role in {"fast_check", "full_build"}]
    preflight = [task for task in manifest.tasks if task.role == "bootstrap"]
    return manifest.app_config_protocol == 1 and bool(checks) and all(
        task.cwd == "." and task.argv[0] in _NODE_COMMANDS for task in [*preflight, *checks]
    )


# This program is passed directly to Python inside the existing fenced command
# execution. No workspace module is imported and no package is downloaded.
NEXT_TYPE_PREPARATION_PY = r'''
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

SUPPORTED_NEXT = "15.5.24"
MAX_SOURCE_BYTES = 1024 * 1024
# The locked MAX template, in its two Git checkout newline representations.
# This is an allowlist, not normalization of the source proof before/after CLI.
DEFAULT_CONFIG_HASHES = {
    "2a119d426c7c2c0c91c6d1905eec5567675f168e6077075d57cd2322f25d89ea",
    "f0e9586e6cda62d7e308344f85afd3b52c1c93967c9fe2ea7b5fc784866ba900",
}


def read_regular(root, name, required=True):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
    except FileNotFoundError:
        if not required:
            return None
        raise RuntimeError("Next type preparation requires existing source configuration") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > MAX_SOURCE_BYTES):
            raise RuntimeError(
                "Next type preparation rejected nonregular, linked or oversized source")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            content = stream.read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            raise RuntimeError("Next type preparation rejected oversized source")
        return content
    finally:
        os.close(fd)


def prune_types(root):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        next_fd = os.open(".next", flags, dir_fd=root)
    except FileNotFoundError:
        return
    try:
        try:
            info = os.stat("types", dir_fd=next_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Next type preparation rejected a non-directory type cache")
        # rmtree itself rechecks the inode after opening it and never follows a
        # replacement symlink. The anchored .next descriptor also survives a
        # concurrent rename without traversing a new workspace path.
        shutil.rmtree("types", dir_fd=next_fd)
    finally:
        os.close(next_fd)


def config_sources(root):
    current = {name: read_regular(root, name, required=False)
               for name in ("next.config.ts", "next.config.js", "next.config.mjs",
                            "next.config.cjs", "next.config.cts", "next.config.mts",
                            "next.config.json")}
    if any(raw is not None for name, raw in current.items() if name != "next.config.ts"):
        return None
    configured = current["next.config.ts"]
    if (configured is not None
            and hashlib.sha256(configured).hexdigest() not in DEFAULT_CONFIG_HASHES):
        return None
    return current


def source_fingerprints(root, configs):
    sources = {name: read_regular(root, name)
               for name in ("package.json", "tsconfig.json", "next-env.d.ts")}
    sources.update(configs)
    return {name: hashlib.sha256(raw).hexdigest() if raw is not None else None
            for name, raw in sources.items()}


def main():
    if (os.name != "posix" or not shutil.rmtree.avoids_symlink_attacks
            or os.open not in os.supports_dir_fd):
        raise RuntimeError("Next type preparation requires fd-relative no-follow support")
    root = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        raw = read_regular(root, "package.json", required=False)
        package = json.loads(raw) if raw is not None else {}
        versions = [package.get(group, {}).get("next")
                    for group in ("dependencies", "devDependencies")]
        if SUPPORTED_NEXT not in versions or any(
                version not in (None, SUPPORTED_NEXT) for version in versions):
            print("Next type preparation not applicable: unsupported or non-Next package")
            return 0
        configs = config_sources(root)
        if configs is None:
            print("Next type preparation not applicable: custom or unsupported config")
            return 0
        # Reject external hardlink aliases before any child process can rewrite
        # source configuration. The later fingerprints remain byte-exact.
        before = source_fingerprints(root, configs)
        # Resolve the installed CLI, not an npx download or a mutable PATH entry.
        resolver = r"""
const fs = require('node:fs');
const path = require('node:path');
const file = require.resolve('next/package.json', { paths: [process.cwd()] });
const pkg = JSON.parse(fs.readFileSync(file, 'utf8'));
if (pkg.version !== '15.5.24') throw Error('Installed Next version is not the starter pin');
process.stdout.write(path.join(path.dirname(file), 'dist/bin/next'));
"""
        resolved = subprocess.run(["node", "-e", resolver], check=True,
                                  stdout=subprocess.PIPE, text=True)
        cli = resolved.stdout
        if not cli or "\n" in cli or "\r" in cli or not os.path.isabs(cli):
            raise RuntimeError("Next type preparation could not resolve the installed CLI")
        prune_types(root)
        generated = subprocess.run(["node", cli, "typegen"], check=False)
        after_configs = config_sources(root)
        if after_configs is None:
            raise RuntimeError("Next type generation changed source configuration")
        after = source_fingerprints(root, after_configs)
        if before != after:
            raise RuntimeError(
                "Next type generation changed source configuration; source proof required")
        if generated.returncode:
            return generated.returncode
        print("Next route type cache regenerated; source configuration unchanged")
        return 0
    finally:
        os.close(root)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # A stable controller diagnosis is enough; paths and source contents are
        # not required. The child CLI retains its ordinary compiler diagnostics.
        reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print("Next type preparation failed: " + reason, file=sys.stderr)
        sys.exit(1)
'''
