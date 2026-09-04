"""Main MAX stack defaults, without product UI or credential-bearing server code."""

import json

from omnia_orchestrator.core.project_machine import MachineManifest


def next_machine_manifest() -> MachineManifest:
    return MachineManifest.model_validate(
        {
            "version": 1,
            "tasks": [
                {
                    "name": "install",
                    "role": "bootstrap",
                    "argv": ["pnpm", "install", "--frozen-lockfile"],
                    "timeout_seconds": 900,
                },
                {
                    "name": "typecheck",
                    "role": "fast_check",
                    "argv": ["pnpm", "typecheck"],
                    "timeout_seconds": 180,
                },
                {
                    "name": "targeted-test",
                    "role": "fast_check",
                    "argv": ["pnpm", "test"],
                    "timeout_seconds": 300,
                },
                {
                    "name": "build",
                    "role": "full_build",
                    "argv": ["pnpm", "build"],
                    "timeout_seconds": 600,
                },
                {
                    "name": "final-test",
                    "role": "full_build",
                    "argv": ["pnpm", "test"],
                    "timeout_seconds": 300,
                },
            ],
            "services": [
                {
                    "name": "web",
                    "argv": ["pnpm", "start"],
                    "readiness": {
                        "port": 3000,
                        "path": "/api/omnia/health",
                        "timeout_seconds": 120,
                    },
                }
            ],
            "routes": [{"path": "/", "service": "web", "port": 3000}],
        }
    )


def next_machine_seed(template: dict[str, str]) -> dict[str, str]:
    # A scaffold selection, not a package allowlist: the agent owns package.json
    # and may add compatible dependencies or its own server/helper source.
    keep = {
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "next-env.d.ts",
        "next.config.ts",
        "postcss.config.mjs",
        "src/app/layout.tsx",
        "src/app/globals.css",
        "src/app/api/omnia/health/route.ts",
        "src/components/MaxAppProvider.tsx",
        "src/components/OmniaCompliance.tsx",
        "src/lib/max/bridge.ts",
        "src/lib/omnia/client.ts",
        "src/lib/omnia/max-config.ts",
    }
    files = {
        path: content
        for path, content in template.items()
        if path in keep
        or path.startswith(("public/", "src/app/legal/", "src/app/support/", "tests/"))
    }
    package = json.loads(files["package.json"])
    package["scripts"].pop("db:push", None)
    package["scripts"].pop("db:generate", None)
    package["scripts"]["test"] = "node --test tests/*.test.mjs"
    files["package.json"] = json.dumps(package, indent=2) + "\n"
    files["next.config.ts"] = files["next.config.ts"].replace(
        "reactStrictMode: true,", "reactStrictMode: true,\n  experimental: { cpus: 2 },"
    )
    files[".omnia/cell.json"] = next_machine_manifest().model_dump_json(indent=2)
    # The browser needs a type, not the platform's cookie-signing implementation.
    files["src/lib/max/session.ts"] = (
        "export type MaxSessionUser = { id: string; firstName: string; lastName: string | null; "
        "username: string | null; languageCode: string | null; photoUrl: string | null; };\n"
    )
    return files
