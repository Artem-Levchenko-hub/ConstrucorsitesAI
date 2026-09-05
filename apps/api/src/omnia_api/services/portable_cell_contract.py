"""Technology-neutral guidance used only for an enabled, selected Cell provider."""

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

RequestedCapability = tuple[str, str, tuple[str, ...]]


class PortableGuideExecutor(Protocol):
    @property
    def capabilities(self) -> Mapping[str, object]: ...

    @property
    def snapshot_files(self) -> Callable[[], Awaitable[Mapping[str, str]]]: ...

PORTABLE_CELL_GUIDE = """
MAX PLATFORM CORE CONTRACT — EXTENSIBLE MAIN STACK
PROJECT CELL: MAX Next.js / React / TypeScript, Node.js 22 and pnpm 9.15.0.
Build the product on this primary stack first. The selected provider supports
public package installation and persistent Linux userland. package.json and lock
files are project-controlled: add compatible libraries as needed. Use auxiliary
tools/processes only when the task needs them; do not replace the whole UI stack.
New pristine projects have .omnia/cell.json with bootstrap/fast/full/start defaults.
Create src/app/page.tsx and the complete product, plus meaningful tests/*.test.mjs.
If an older untouched seed lacks the manifest, use the Next commands below first.
Existing projects without this manifest keep their legacy Next.js behavior until
you intentionally migrate them. Do not run legacy pnpm/typecheck assumptions for
a portable project. This capability is provider-enforced, not permission to use
host Docker, host files, devices, privileged flags or other projects' networks.

Default manifest (extend only when a real helper/service is necessary):
{"version":1,"tasks":[
 {"name":"install","role":"bootstrap","argv":["pnpm","install","--frozen-lockfile"],"timeout_seconds":900},
 {"name":"typecheck","role":"fast_check","argv":["pnpm","typecheck"],"timeout_seconds":180},
 {"name":"lint","role":"fast_check","argv":["pnpm","lint"],"timeout_seconds":180},
 {"name":"build","role":"full_build","argv":["pnpm","build"],"timeout_seconds":600},
 {"name":"final-test","role":"full_build","argv":["pnpm","test"],"timeout_seconds":300}],
 "services":[{"name":"web","argv":["pnpm","start"],
 "readiness":{"port":3000,"path":"/","timeout_seconds":120}}],
 "routes":[{"path":"/","service":"web","port":3000}]}

argv is explicit, cwd is relative to /workspace. Use sh -lc explicitly if needed.
Each role runs only its declared tasks; full_build requires a real final test.
Final tests must preserve production build output. Never run next dev against
.next after next build: it erases the production build. Use a separate distDir
for a test dev server, or test next start on a separate port and terminate it.
For small database features, direct SQL integration tests avoid rebuilding a
second web server. Do not weaken or skip actual persistence/isolation checks.
bash executes in this same machine. Never use a fake
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
A dedicated project PostgreSQL is reachable at 127.0.0.1 via DATABASE_URL with
full admin access. It is isolated from the managed MAX core PostgreSQL and from
other projects. Manage your own schema, migrations, roles, settings and bundled
extensions there. The platform snapshots and restore-smokes its disk. Database
superuser access does not grant container, host or managed-platform privileges.

MAX AUTH AND MANAGED INTEGRATIONS ARE A SEPARATE TRUSTED BOUNDARY.
PostgreSQL-backed managed APIs remain in the trusted core. /api/max/* and /api/omnia/* stay
platform-owned, as do /__omnia/* and /auth. Product routes cannot replace them.
The browser uses same-origin authenticated fetch for the existing managed MAX
APIs. Keep MAX Bridge/session initialization for real MAX launches; signed owner
preview is supplied by the platform. Product servers receive trusted
X-Omnia-User-ID, X-Omnia-Project-ID and X-Omnia-Session-Epoch from the gateway,
not a signing key or managed PostgreSQL password. For Next.js product routes,
getMaxUser from @/lib/max/session is preconfigured to read this trusted identity;
use it rather than implementing cookie validation in the product. Never trust a user id in a
request body. Product-owned storage must scope records by the trusted user id.
Managed actions are GET/POST /api/omnia/actions; existing MAX client source can
be read for exact request shapes. Managed integrations use the reserved browser
API, never embedded provider keys. The machine cannot contact managed PostgreSQL.
Use the dedicated DATABASE_URL above for product data. Do not write credential
files or provider secrets, disable auth, forge evidence, fabricate user history
or simulate integrations. Own product data/SQLite/service volumes are allowed;
keep backup/recovery checks honest.

Create the requested UI, behavior, navigation and real failure/empty states from
scratch. A manifest, starter server, decorative tabs or bundled core is not the
product. Run build and runtime_check after the final source write. runtime_check
independently verifies the product HTTP route, signed MAX session, protected data
read, rejected anonymous/bad-cookie access and trusted project identity. No see
tool exists. Do not claim deployment, payment or business integrations untested.
Proof invalidation follows observed before/after source, dependency, schema,
manifest and environment digests. Clean commands keep their proof. No
screenshot/see tools are available.
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
    legacy: str, executor: PortableGuideExecutor, *, new_product: bool = False
) -> str:
    # History is not selection: customized legacy source may have no snapshots.
    files = await executor.snapshot_files()
    return machine_stack_guide(legacy, executor.capabilities, files)


def portable_source_gap(
    files: Mapping[str, str], capabilities: list[RequestedCapability]
) -> str | None:
    try:
        manifest = json.loads(files.get(".omnia/cell.json", ""))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError
        tasks = manifest.get("tasks", [])
        if not any(
            isinstance(task, dict)
            and (
                task.get("role") == "test"
                or (
                    task.get("role") == "full_build"
                    and "test" in str(task.get("name", "")).lower()
                )
            )
            and task.get("argv")
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
