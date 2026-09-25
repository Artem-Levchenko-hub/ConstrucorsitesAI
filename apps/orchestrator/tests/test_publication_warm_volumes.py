"""B2 (P06/P08): a warm publication moves the workspace and home; the pnpm and
corepack stores only when a startup command may resolve packages."""

from __future__ import annotations

from types import SimpleNamespace

from tests.test_project_machine_manifest import payload
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.services.cell_publication import (
    runtime_needs_package_stores,
    warm_volume_names,
)


def _manifest(*argvs: list[str]) -> MachineManifest:
    """The fixture manifest's services (api, worker) with replaced commands."""
    value = payload()
    services = value["services"][: len(argvs)]
    for service, argv in zip(services, argvs, strict=True):
        service["argv"] = argv
    value["services"] = services
    return MachineManifest.model_validate(value)


SOURCE = SimpleNamespace(
    workspace_volume="stem-workspace",
    stem="stem",
    pnpm_cache_volume="stem-pnpm",
    corepack_cache_volume="stem-corepack",
)


def test_plain_start_commands_leave_the_stores_behind():
    plain = (["pnpm", "start"], ["node", "server.js"], ["pnpm", "run", "start"], ["next", "start"])
    for argv in plain:
        assert runtime_needs_package_stores(_manifest(argv)) is False, argv
    names = warm_volume_names(SOURCE, _manifest(["pnpm", "start"]))
    assert names == ("stem-workspace", "stem-home")


def test_startup_commands_that_resolve_packages_keep_the_stores():
    for argv in (
        ["sh", "-c", "pnpm install --prod && pnpm start"],
        ["pnpm", "i"],
        ["npx", "serve", "out"],
        ["pnpm", "dlx", "serve"],
        ["sh", "-lc", "corepack pnpm exec next start"],
    ):
        assert runtime_needs_package_stores(_manifest(argv)) is True, argv
    names = warm_volume_names(SOURCE, _manifest(["pnpm", "start"], ["pnpm", "install"]))
    assert names == ("stem-workspace", "stem-home", "stem-pnpm", "stem-corepack")
