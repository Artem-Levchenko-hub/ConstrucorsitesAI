import json
from pathlib import Path

from omnia_orchestrator.core.project_machine import MachineManifest


def test_default_max_environment_is_next_with_extensible_dependencies_not_product_ui():
    from omnia_orchestrator.services.machine_defaults import next_machine_seed

    template = Path(__file__).parents[1] / "templates" / "max-miniapp-nextjs"
    files = {
        str(path.relative_to(template)).replace("\\", "/"): path.read_text(encoding="utf-8")
        for path in template.rglob("*")
        if path.is_file()
    }
    seeded = next_machine_seed(files)
    manifest = MachineManifest.from_files(seeded)
    assert manifest is not None
    assert manifest.services[0].argv == ["pnpm", "start"]
    assert manifest.routes[0].port == 3000
    assert [task.role for task in manifest.tasks] == ["bootstrap", "build", "test"]
    package = json.loads(seeded["package.json"])
    assert package["engines"]["node"] == ">=22"
    assert package["packageManager"] == "pnpm@9.15.0"
    assert package["scripts"]["test"] == "node --test tests/*.test.mjs"
    assert "src/app/page.tsx" not in seeded
    assert "src/components/MaxAppProvider.tsx" in seeded
    assert "src/lib/db/index.ts" not in seeded
    assert not any(path.startswith("src/app/api/") for path in seeded)
    assert "AUTH_SECRET" not in "\n".join(seeded.values())


def test_machine_base_has_ready_pinned_node_and_pnpm_plus_internal_python():
    recipe = (Path(__file__).parents[1] / "scripts" / "Dockerfile.project-machine").read_text()
    assert "FROM ${NODE_BASE}" in recipe
    assert "ARG NODE_BASE=node:22-slim" in recipe
    assert "pnpm@9.15.0" in recipe
    assert "python3" in recipe
    assert "node --version" in recipe
    assert recipe.index("require('tls').rootCertificates") < recipe.index("apt-get")
    assert "Verify-Peer=false" not in recipe
