"""Technology-neutral guidance used only for an enabled, selected Cell provider."""

import json
from collections.abc import Mapping

PORTABLE_CELL_GUIDE = """
MAX PLATFORM CORE CONTRACT — EXTENSIBLE MAIN STACK
PROJECT CELL: MAX Next.js / React / TypeScript, Node.js 22 and pnpm 9.15.0.
Build the product on this primary stack first. The selected provider supports
public package installation and persistent Linux userland. package.json and lock
files are project-controlled: add compatible libraries as needed. Use auxiliary
tools/processes only when the task needs them; do not replace the whole UI stack.
New pristine projects have .omnia/cell.json with install/build/test/start defaults.
Create src/app/page.tsx and the complete product, plus meaningful tests/*.test.mjs.
If an older untouched seed lacks the manifest, use the Next commands below first.
Existing projects without this manifest keep their legacy Next.js behavior until
you intentionally migrate them. Do not run legacy pnpm/typecheck assumptions for
a portable project. This capability is provider-enforced, not permission to use
host Docker, host files, devices, privileged flags or other projects' networks.

Default manifest (extend only when a real helper/service is necessary):
{"version":1,"tasks":[
 {"name":"install","role":"bootstrap","argv":["pnpm","install","--no-frozen-lockfile"],"timeout_seconds":900},
 {"name":"build","role":"build","argv":["pnpm","build"],"timeout_seconds":900},
 {"name":"test","role":"test","argv":["pnpm","test"]}],
 "services":[{"name":"web","argv":["pnpm","start"],
 "readiness":{"port":3000,"path":"/","timeout_seconds":120}}],
 "routes":[{"path":"/","service":"web","port":3000}]}

argv is explicit, cwd is relative to /workspace. Use sh -lc explicitly if needed.
bootstrap/build/test tasks run in declared order; build runs all three roles and
requires actual test tasks. bash executes in this same machine. Never use a fake
test exit or claim build success without exercising product behavior. Services
are controller-supervised after runtime_check applies the checked manifest;
bind HTTP services to 0.0.0.0, not only localhost. Do not daemonize a service argv.
Public HTTP(S)/CONNECT package traffic must honor HTTP_PROXY/HTTPS_PROXY. Raw
outbound sockets, private/metadata/host/platform/cross-cell destinations and Docker
DNS are blocked. No package/language allowlist. Root can install guest userland,
but cannot change the trusted network guard. Installer failures include log tails.
Source, /root caches, system-installed userland and declared named volumes are
retained via checked immutable environment artifacts. /run, /tmp and logs are
ephemeral. Services may declare depends_on and mounts [{"volume":"data",
"target":"/data"}]. One named volume has one guest target. Resource limits are
aggregate; defaults per service: .25 CPU,128 MiB RAM,1 GiB disk admission,64 pids.
RAM/CPU/pids are enforced; disk is admission/snapshot bounded, not a hard quota.

MAX AUTH AND MANAGED INTEGRATIONS ARE A SEPARATE TRUSTED BOUNDARY.
PostgreSQL-backed managed APIs remain in the trusted core. /api/max/* and /api/omnia/* stay
platform-owned, as do /__omnia/* and /auth. Product routes cannot replace them.
The browser uses same-origin authenticated fetch for the existing managed MAX
APIs. Keep MAX Bridge/session initialization for real MAX launches; signed owner
preview is supplied by the platform. Product servers receive trusted
X-Omnia-User-ID, X-Omnia-Project-ID and X-Omnia-Session-Epoch from the gateway,
not a signing key or managed PostgreSQL password. Never trust a user id in a
request body. Product-owned storage must scope records by the trusted user id.
Managed actions are GET/POST /api/omnia/actions; existing MAX client source can
be read for exact request shapes. Managed integrations use the reserved browser
API, never embedded provider keys. The machine cannot contact managed PostgreSQL.
Do not write credential files or provider secrets, disable auth, forge evidence,
fabricate user history or simulate integrations. Own product data/SQLite/service
volumes are allowed; keep backup/recovery checks honest.

Create the requested UI, behavior, navigation and real failure/empty states from
scratch. A manifest, starter server, decorative tabs or bundled core is not the
product. Run build and runtime_check after the final source write. runtime_check
independently verifies the product HTTP route, signed MAX session, protected data
read, rejected anonymous/bad-cookie access and trusted project identity. No see
tool exists. Do not claim deployment, payment or business integrations untested.
Any shell/build may mutate installed packages or running processes even without a
source diff. Run fresh build and runtime_check after those actions. A failed or
timed-out command invalidates prior proof too. No screenshot/see tools are available.
""".strip()


def machine_stack_guide(
    legacy: str,
    capabilities: Mapping[str, object],
    files: Mapping[str, str],
    *,
    new_product: bool = False,
) -> str:
    if capabilities.get("portable_machine") is True and ".omnia/cell.json" in files:
        return PORTABLE_CELL_GUIDE
    return legacy


async def machine_stack_guide_from_executor(
    legacy: str, executor, *, new_product: bool = False
) -> str:
    # History is not selection: customized legacy source may have no snapshots.
    files = await executor.snapshot_files()
    return machine_stack_guide(legacy, executor.capabilities, files)


def portable_source_gap(files: Mapping[str, str], capabilities) -> str | None:
    try:
        manifest = json.loads(files.get(".omnia/cell.json", ""))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError
        tasks = manifest.get("tasks", [])
        if not any(
            isinstance(task, dict) and task.get("role") == "test" and task.get("argv")
            for task in tasks
        ):
            return "Portable product requires a declared test task that checks its behavior."
        if not manifest.get("services") or not any(
            route.get("path") == "/" for route in manifest.get("routes", [])
        ):
            return "Portable product needs a declared service and root HTTP route."
    except (ValueError, TypeError, AttributeError):
        return "Write a valid .omnia/cell.json portable manifest before done."
    source = {
        path: content
        for path, content in files.items()
        if not path.startswith(
            (
                ".omnia/",
                "src/lib/max/",
                "src/lib/omnia/",
                "src/lib/db/",
                "src/app/api/max/",
                "src/app/api/omnia/",
            )
        )
        and path not in {"package.json", "pnpm-lock.yaml", "package-lock.json", "SYSTEM_PROMPT.md"}
        and not path.endswith((".md", ".lock", ".json"))
        and len(content.strip()) >= 40
    }
    if not source:
        return "Portable manifest is present but actual product implementation is missing."
    corpus = "\n".join(source.values()).lower()
    missing = [
        label
        for _key, label, needles in capabilities
        if not any(word in corpus for word in needles)
    ]
    return (
        "Explicit brief capabilities are still missing: " + ", ".join(missing) if missing else None
    )
