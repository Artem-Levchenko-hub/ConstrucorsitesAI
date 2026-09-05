"""Isolated, real-Docker publication acceptance; no model or real MAX credentials.

Run on the reviewed Linux host from apps/orchestrator, with its documented
environment loaded, immutable --core-image and a private --qa-parent directory:

  uv run python scripts/smoke_cell_publication.py --run --qa-parent /tmp/omnia-qa \
    --core-image sha256:<reviewed-image> --compact-qa-resources

Default resource profile is unchanged. --compact-qa-resources applies ONLY to this
tiny Node/pg fixture: source reserves 1.9 CPU / 2.25 GiB; publication uses its
configured lean profile. Actual combined budget is printed before provisioning,
plus unchanged host reserves and normal admission. This is functional
acceptance, NOT a production-capacity proof. Dependency install needs public npm
egress. Real nginx writes are limited to a fresh canary hostname; TLS validation
is enabled. The script never calls MAX Bot API or subscribes a webhook.

Deadline defaults to 45 minutes. --keep (the default) retains private artifacts
and resources for review. --cleanup-on-success removes only fresh canary-owned containers,
networks, volumes and its single nginx route; snapshots/private evidence stay.
Never pass an existing cell UUID, hostname or journal. None are accepted.
"""

# Embedded JavaScript is deliberately kept as executable fixture source.
# ruff: noqa: E501, ASYNC240
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

_SERVER = r"""
const http = require('node:http');
const {Pool} = require('pg');
const pool = new Pool({connectionString: process.env.DATABASE_URL, max: 2,
  connectionTimeoutMillis: 5000, statement_timeout: 5000});
const version = 'VERSION_MARKER';
http.createServer(async (req, res) => {
  const reply = (status, body) => {res.writeHead(status, {'Content-Type':'application/json'});
    res.end(JSON.stringify(body));};
  try {
    if (req.url === '/') return reply(200, {version});
    if (req.url !== '/api/records') return reply(404, {error:'not found'});
    const user = req.headers['x-omnia-user-id'];
    if (!/^[0-9]+$/.test(user || '')) return reply(401, {error:'authentication required'});
    if (req.method === 'GET') {
      const result = await pool.query(
        'SELECT name,value FROM publication_canary WHERE owner_id=$1 ORDER BY name', [user]);
      return reply(200, {version, records:result.rows});
    }
    if (req.method === 'POST') {
      let text = '';
      for await (const part of req) {text += part; if (text.length > 4096) return reply(413, {});}
      const body = JSON.parse(text);
      if (typeof body.name !== 'string' || typeof body.value !== 'string' ||
          body.name.length > 64 || body.value.length > 128) return reply(400, {});
      await pool.query('INSERT INTO publication_canary(owner_id,name,value) VALUES($1,$2,$3) '
        + 'ON CONFLICT(owner_id,name) DO UPDATE SET value=excluded.value',
        [user,body.name,body.value]);
      return reply(201, {saved:true});
    }
    reply(405, {});
  } catch (_) {reply(500, {error:'operation failed'});}
}).listen(8080, '0.0.0.0');
"""
_SEED = r"""
const {Pool} = require('pg');
const pool = new Pool({connectionString:process.env.DATABASE_URL});
(async () => {
 await pool.query('CREATE TABLE IF NOT EXISTS publication_canary('
   + 'owner_id text NOT NULL,name text NOT NULL,value text NOT NULL,PRIMARY KEY(owner_id,name))');
 await pool.query('INSERT INTO publication_canary VALUES($1,$2,$3) ON CONFLICT DO NOTHING',
   ['10001','initial','source-seed']);
 await pool.end();
})().catch(() => {console.error('fixture database seed failed');process.exitCode=1;});
"""
_CHECK = r"""
const {Pool} = require('pg');
const pool = new Pool({connectionString:process.env.DATABASE_URL});
(async () => {
 const result = await pool.query('SELECT count(*)::int AS n FROM publication_canary');
 if (result.rows[0].n < 1) throw new Error('missing source row');
 await pool.end();
})().catch(() => {console.error('fixture database check failed');process.exitCode=1;});
"""
_BUILD = r"""
const {spawnSync} = require('node:child_process');
for (const argv of [
  ['npm','install','--ignore-scripts','--no-audit','--no-fund'],
  ['node','seed.cjs'], ['node','--check','server.cjs'], ['node','check.cjs']
]) {
  const result = spawnSync(argv[0], argv.slice(1), {stdio:'inherit', timeout:240000});
  if (result.status !== 0) process.exit(result.status || 1);
}
"""


def fixture_files(version: str) -> dict[str, str]:
    manifest = {
        "version": 1,
        "tasks": [
            {"name": "accepted-build", "role": "full_build", "argv": ["node", "build.cjs"],
             "timeout_seconds": 600},
            {"name": "data-test", "role": "test", "argv": ["node", "check.cjs"]},
        ],
        "services": [{"name": "web", "argv": ["node", "server.cjs"],
                      "readiness": {"port": 8080, "path": "/"}}],
        "routes": [{"path": "/", "service": "web", "port": 8080}],
    }
    return {
        ".omnia/cell.json": json.dumps(manifest),
        "package.json": json.dumps({"name": "publication-canary", "version": "1.0.0",
                                    "private": True, "dependencies": {"pg": "8.13.1"}}),
        "server.cjs": _SERVER.replace("VERSION_MARKER", version),
        "seed.cjs": _SEED, "check.cjs": _CHECK, "build.cjs": _BUILD,
    }


def business_config() -> dict:
    return {
        "app_name": "Publication canary", "app_type": "custom", "summary": "Isolated QA",
        "audience": "QA", "primary_action": "Open", "features": [], "style": "clean",
        "brand_colors": "", "content": [],
        "operator": {"legal_name": "QA fixture", "inn": "", "ogrn": "", "address": ""},
        "support": {"email": None, "phone": "", "response_time": "QA only"},
        "legal": {"age_rating": "0+", "has_sales": False, "has_user_content": False,
                  "marketing_notifications": False, "personal_data_consent": True,
                  "terms_accepted": True},
    }


class CanaryError(RuntimeError):
    """Only authored, non-sensitive diagnostic messages may cross this boundary."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CanaryError(message)


def host_budget(allocated: float, required: float, reserve: float, total: float, kind: str) -> None:
    require(all(value >= 0 for value in (allocated, required, reserve, total)), "invalid host budget")
    require(allocated + required + reserve <= total + 1e-9,
            f"host preflight refused: insufficient {kind} including canonical reservations and host reserve")


def ownership(resource, allowed: set[UUID], project: UUID, owner: UUID) -> None:
    """No destructive Docker call is allowed before this exact identity check."""
    resource.reload()
    attrs = resource.attrs
    labels = attrs.get("Config", {}).get("Labels") or attrs.get("Labels") or {}
    try:
        workspace = UUID(labels.get("omnia.workspace_id", ""))
    except ValueError:
        raise CanaryError("cleanup refused: resource has no canary workspace") from None
    require(workspace in allowed, "cleanup refused: workspace is not canary-owned")
    require(labels.get("omnia.project_id") == str(project), "cleanup refused: project mismatch")
    require(labels.get("omnia.owner_id") == str(owner), "cleanup refused: owner mismatch")
    name = resource.name
    test_prefixes = (f"omnia-cell-test-{workspace.hex}-", f"omnia-machine-test-{workspace.hex}-")
    release_prefix = f"omnia-machine-{workspace.hex}-"
    require(
        name.startswith(test_prefixes)
        or (labels.get("omnia.namespace") == "test" and name.startswith(release_prefix)),
        "cleanup refused: resource name is outside canary namespace",
    )
    require(labels.get("omnia.namespace") in (None, "test"), "cleanup refused: live namespace")


def container_diagnostic(container, allowed: set[UUID], project: UUID, owner: UUID) -> dict:
    """Allowlisted runtime facts only; never persist Docker Env/Error or raw logs."""
    ownership(container, allowed, project, owner)
    labels = container.attrs["Config"]["Labels"]
    state = container.attrs.get("State", {})
    observed = {
        "name": container.name,
        "workspace_id": labels["omnia.workspace_id"],
        "kind": labels.get("omnia.resource_kind"),
        "state": {"status": state.get("Status"), "exit_code": state.get("ExitCode"),
                  "oom_killed": state.get("OOMKilled")},
    }
    if labels.get("omnia.resource_kind") == "namespace-guard":
        try:
            # Bounded log read; retain marker count only, never policy hash/text.
            count = container.logs(stdout=True, stderr=True, tail=200).count(b"POLICY_READY=")
            observed.update(policy_ready=bool(count), policy_ready_count=count)
        except Exception as error:
            observed["policy_read_error_type"] = type(error).__name__
    return observed


def capture_failure_containers(client, root: Path, allowed: set[UUID], project: UUID, owner: UUID) -> None:
    """Capture before any optional cleanup, refusing foreign resource identities."""
    target = root / "failure-containers.json"
    try:
        containers = []
        for workspace in allowed:
            containers.extend(client.containers.list(
                all=True, filters={"label": f"omnia.workspace_id={workspace}"}))
        for container in containers:
            ownership(container, allowed, project, owner)
        observations = []
        for container in containers:
            try:
                observations.append(container_diagnostic(container, allowed, project, owner))
            except Exception as error:
                # Containers may disappear during failed automatic recovery.
                observations.append({"capture_error_type": type(error).__name__})
        payload = {"containers": observations, "policy_log_tail_lines": 200}
    except Exception as error:
        payload = {"capture_refused_or_failed": type(error).__name__}
    target.touch(mode=0o600, exist_ok=True)
    target.chmod(0o600)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def launch_data(token: str, user: str) -> str:
    pairs = {"auth_date": str(int(time.time())), "query_id": "qa-" + secrets.token_hex(8),
             "user": json.dumps({"id": int(user), "first_name": "QA"}, separators=(",", ":"))}
    key = hmac.digest(b"WebAppData", token.encode(), "sha256")
    pairs["hash"] = hmac.digest(key, "\n".join(f"{k}={v}" for k, v in sorted(pairs.items())).encode(), "sha256").hex()
    return urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)


def signed_cookie(secret: str, user: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({
        "id": user, "expiresAt": int(time.time()) + 120,
    }).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.digest(secret.encode(), payload.encode(), "sha256")).decode().rstrip("=")
    return "__Host-max_session=" + payload + "." + signature


def http(url: str, path: str, *, cookie: str = "", body: dict | None = None, html=False,
         extra_headers: dict | None = None):
    headers = {"Accept": "text/html" if html else "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    if cookie:
        headers["Cookie"] = cookie
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url + path, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read(1024 * 1024), response.headers


async def login(url: str, token: str, user: str) -> str:
    status, _, headers = await asyncio.to_thread(http, url, "/api/max/session", body={"initData": launch_data(token, user)})
    require(status == 200, f"MAX session exchange failed: HTTP {status}")
    raw = headers.get("Set-Cookie", "")
    require("HttpOnly" in raw and "Secure" in raw, "MAX session lacks secure HttpOnly cookie")
    return raw.split(";", 1)[0]


async def assert_records(url: str, cookie: str, version: str, expected: list[dict]) -> None:
    status, body, _ = await asyncio.to_thread(http, url, "/api/records", cookie=cookie)
    require(status == 200, f"business read failed: HTTP {status}")
    require(json.loads(body) == {"version": version, "records": expected}, "business readback mismatch")


async def run(args) -> None:
    import docker

    from omnia_orchestrator.core.cell_resources import LifecycleMutation
    from omnia_orchestrator.core.config import Settings, get_settings
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
    from omnia_orchestrator.routers.workspace import (
        _read_agent_workspace_files,
        _workspace_revision,
    )
    from omnia_orchestrator.schemas.cell_publication import CellDeployRequest
    from omnia_orchestrator.services import machine_adapter, nginx_writer
    from omnia_orchestrator.services.cell_publication import CellPublicationService
    from omnia_orchestrator.services.cell_reservations import CellCapacityReservationStore
    from omnia_orchestrator.services.cell_state import CellStateStore
    from omnia_orchestrator.services.docker_machine_backend import _archive_file
    from omnia_orchestrator.services.published_machine_backend import PublishedMachineBackend
    from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider

    require(os.name == "posix", "Linux host with local Docker is required")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", args.core_image) is not None, "core image must be immutable")
    parent = Path(args.qa_parent).resolve(strict=True)
    require(parent.is_dir() and parent not in (Path("/"), Path.home()), "QA parent must be a dedicated directory")
    require(parent.stat().st_mode & 0o077 == 0, "QA parent must be private (mode 0700)")
    root = Path(tempfile.mkdtemp(prefix="pytest-omnia-publication-", dir=parent))
    root.chmod(0o700)
    # CapacityReader.statvfs checks this path before ensure() creates state.
    # Creating the private evidence directory allocates no Docker resources.
    (root / "state").mkdir(mode=0o700)
    source_id, project, owner = uuid4(), uuid4(), uuid4()
    allowed = {source_id}
    slug = "qa-publish-" + project.hex[:20]
    cfg = get_settings()
    updates = {"cell_state_path": str(root / "state" / "project-cells.json"),
               "workspace_provider": "docker_owner_canary", "docker_owner_canary_enabled": True,
               "cell_machine_enabled": True, "cell_profile_version": "docker-owner-cell-resources-v2"}
    if args.compact_qa_resources:
        updates.update(cell_bundle_cpu_cores=1.0, cell_bundle_memory_bytes=1024**3,
                       cell_active_machine_cpu_cores=0.5, cell_active_machine_memory_bytes=512 * 1024**2,
                       cell_project_postgres_cpu_cores=0.1, cell_project_postgres_memory_bytes=128 * 1024**2,
                       cell_helper_cpu_cores=0.2, cell_helper_memory_bytes=128 * 1024**2,
                       cell_managed_core_cpu_cores=0.35, cell_managed_core_memory_bytes=768 * 1024**2,
                       cell_required_free_disk_bytes=4 * 1024**3)
    settings = Settings.model_validate({**cfg.model_dump(), **updates})
    provider = build_workspace_provider(settings)
    manager = provider.resource_manager
    require(manager is not None and manager.namespace == "test", "QA manager must use test namespace")
    adapter = manager.machine_runtime
    require(adapter is not None, "portable adapter unavailable")
    client = docker.DockerClient(base_url=settings.docker_host, timeout=60)
    client.images.get(args.core_image)
    # Per-process immutable fixture image selection; no image build/tag/global daemon mutation.
    original_stack = machine_adapter.get_stack
    machine_adapter.get_stack = lambda _name: SimpleNamespace(image_tag=args.core_image)
    host = nginx_writer.prod_host(slug)
    site = Path(cfg.nginx_sites_dir) / f"{host}.conf"
    require(not site.exists(), "canary nginx host already exists")
    evidence = {"workspace_id": str(source_id), "project_id": str(project), "owner_id": str(owner),
                "slug": slug, "checks": [], "compact_qa_resources": args.compact_qa_resources}

    def record(check: str):
        evidence["checks"].append(check)
        target = root / "evidence.json"
        target.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        target.chmod(0o600)
        print(json.dumps({"check": check, "passed": True}), flush=True)

    def manager_for(workspace: UUID):
        require(workspace in allowed, "publication requested a non-canary manager")
        return manager

    service = CellPublicationService(settings, manager_factory=manager_for)
    production_manager = service._production_manager(source_id)
    token, webhook = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    print("QA_ARTIFACT_ROOT=" + str(root), flush=True)
    print(json.dumps({"combined_cpu": manager.profile.full_quota.cpu_cores + production_manager.profile.full_quota.cpu_cores,
                      "combined_memory_bytes": manager.profile.full_quota.memory_bytes + production_manager.profile.full_quota.memory_bytes}), flush=True)

    def preflight_host() -> None:
        # Read-only canonical ledger. The isolated manager's own admission cannot
        # see it; never interpret isolated admission as host-wide capacity proof.
        canonical_root = CellStateStore(cfg.cell_state_path).root
        ledger = CellCapacityReservationStore(
            canonical_root.parent / f"{canonical_root.name}-capacity-reservations")
        allocated = ledger.totals()
        source_quota = manager.profile.full_quota
        public_quota = production_manager.profile.full_quota
        capacity = manager.capacity_reader.read()
        require(capacity.failure_reason is None, "host capacity cannot be verified")
        cpu = source_quota.cpu_cores + public_quota.cpu_cores
        memory = source_quota.memory_bytes + public_quota.memory_bytes
        print(json.dumps({"host_preflight": True, "canonical_reserved_cpu": allocated.cpu_cores,
                          "other_qa_cpu": args.other_qa_cpu_cores, "required_qa_cpu": cpu,
                          "host_cpu_reserve": cfg.cell_host_cpu_reserve_cores,
                          "host_cpu_total": capacity.cpu_count}), flush=True)
        host_budget(allocated.cpu_cores + args.other_qa_cpu_cores, cpu,
                    cfg.cell_host_cpu_reserve_cores, capacity.cpu_count, "CPU")
        host_budget(allocated.memory_bytes, memory, cfg.cell_host_memory_reserve_bytes,
                    capacity.memory_total_bytes, "memory")
        # Actual memory availability protects non-ledger services, including other QA.
        host_budget(0, memory, cfg.cell_host_memory_reserve_bytes,
                    capacity.memory_available_bytes, "available memory")

    preflight_host()

    async def accepted(version: str, epoch: int) -> CellDeployRequest:
        previous_state = manager.state_store.load(source_id)
        if previous_state is not None:
            epoch = max(epoch, previous_state.fencing_epoch + 1)
        generation = uuid4()
        await provider.ensure(WorkspaceSpec(workspace_id=source_id, project_id=project, owner_id=owner,
                                           profile_version=settings.cell_profile_version,
                                           generation_run_id=generation),
                              LifecycleMutation(uuid4(), epoch, hashlib.sha256(version.encode()).hexdigest()))
        state = manager.state_store.load(source_id)
        files = fixture_files(version)
        manifest = MachineManifest.from_files(files)
        machine, backend = adapter.parts(state)
        await machine.ensure(manifest, LifecycleMutation(uuid4(), epoch, "a" * 64))
        for name, content in files.items():
            require(backend._container().put_archive("/workspace", _archive_file(name, content.encode())) is not False,
                    "source fixture write failed")
        if version == "v2":
            mutation = backend._project_postgres().exec_run([
                "psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-U", "postgres", "-d", "postgres",
                "-c", "UPDATE publication_canary SET value='source-only' WHERE name='initial'",
            ], environment={"PGPASSWORD": backend.project_postgres_password})
            require(mutation.exit_code == 0, "source data mutation failed")
        result = await adapter.apply(state, manifest, SimpleNamespace(
            generation_run_id=generation, fencing_epoch=epoch, expected_revision="a" * 64,
        ))
        require(result.exit_code == 0, "fixture build/test failed; inspect private source operation")
        actual = await _read_agent_workspace_files(manager, backend.workspace_volume)
        revision = _workspace_revision(actual)
        schema = PublishedMachineBackend.schema_digest(backend)
        # The canary bypasses the API candidate store intentionally, but supplies
        # the actual built source digest/schema and actual successful build evidence.
        # commit_sha is a deterministic fixture revision, not a claimed Git commit.
        request = CellDeployRequest(
            workspace_id=source_id, project_id=project, owner_id=owner, snapshot_id=uuid4(),
            candidate_id=uuid4(), slug=slug, commit_sha=hashlib.sha1(revision.encode()).hexdigest(),
            source_revision=revision, fencing_epoch=epoch + 1, accepted_fencing_epoch=epoch,
            proof_key=hashlib.sha256((revision + schema).encode()).hexdigest(), schema_data_digest=schema,
            build_ref=f"qa:built:{revision}", verification_ref=f"qa:db-checked:{schema}",
            idempotency_key="qa-" + uuid4().hex,
            runtime_env={"MAX_BOT_TOKEN": token, "MAX_WEBHOOK_SECRET": webhook,
                         "MAX_API_BASE_URL": "https://platform-api2.max.ru"},
            business_config=business_config(),
        )
        allowed.add(service.production_identity(request))
        await manager.release_generation(source_id, LifecycleMutation(uuid4(), epoch + 1, "b" * 64),
                                         generation_run_id=generation)
        released = manager.state_store.load(source_id)
        require(released is not None and released.active_generation_run_id is None,
                "source generation release was not confirmed")
        if released.bundle_state in {"retained", "resources_paused"}:
            await provider.wake(source_id, LifecycleMutation(
                uuid4(), released.fencing_epoch + 1, "c" * 64,
            ))
            released = manager.state_store.load(source_id)
        require(released is not None and released.bundle_state == "resources_ready"
                and released.phase == "completed" and released.active_generation_run_id is None,
                "source retained bundle is not ready for publication")
        request = request.model_copy(update={"fencing_epoch": released.fencing_epoch})
        record("accepted-source-" + version)
        return request

    async def publish(request):
        response = await service.submit(request)
        async with asyncio.timeout(930):
            while True:
                status = service.get(project)
                require(status is not None and status.run_id == response.run_id, "publication status identity changed")
                if status.phase == "done":
                    require(bool(status.prod_url and status.prod_url.startswith("https://")), "publication lacks HTTPS URL")
                    return status.prod_url
                require(status.phase != "failed", "publication failed; inspect private controller journal")
                await asyncio.sleep(1)

    successful = False
    try:
        async with asyncio.timeout(args.deadline_seconds):
            first = await accepted("v1", 1)
            url = await publish(first)
            public_id = service.production_identity(first)
            require((await asyncio.to_thread(http, url, "/", html=True))[0] == 200, "public launch shell missing")
            require((await asyncio.to_thread(http, url, "/api/records"))[0] == 401, "anonymous business API exposed")
            require((await asyncio.to_thread(http, url, "/api/omnia/preview-session"))[0] == 404, "public preview bootstrap exposed")
            wrong = await asyncio.to_thread(http, url, "/api/max/session", body={"initData": launch_data("wrong-bot-token", "10001")})
            require(wrong[0] == 401, "wrong bot launch data accepted")
            user = await login(url, token, "10001")
            other = await login(url, token, "10002")
            initial = [{"name": "initial", "value": "source-seed"}]
            await assert_records(url, user, "v1", initial)
            await assert_records(url, other, "v1", [])
            record("real-max-exchange-initial-data-and-user-isolation")
            require((await asyncio.to_thread(http, url, "/api/records", cookie=user,
                                             body={"name": "production", "value": "live-write"}))[0] == 201,
                    "production business mutation failed")
            live = [*initial, {"name": "production", "value": "live-write"}]
            await assert_records(url, user, "v1", live)
            await assert_records(url, other, "v1", [])
            # Live metadata and credentials are independent of immutable code/data.
            old_token, old_webhook = token, webhook
            token, webhook = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            updated_config = business_config()
            updated_config["app_name"] = "Publication canary updated"
            updated_config["support"]["response_time"] = "CANARY_SUPPORT_UPDATED"
            configured = await service.configure(
                project, owner,
                runtime_env={"MAX_BOT_TOKEN": token, "MAX_WEBHOOK_SECRET": webhook,
                             "MAX_API_BASE_URL": "https://platform-api2.max.ru"},
                business_config=updated_config, business_config_version=2,
            )
            require(configured.get("applied") is True, "public configuration was not applied")
            stale_login = await asyncio.to_thread(http, url, "/api/max/session",
                                                 body={"initData": launch_data(old_token, "10001")})
            require(stale_login[0] == 401, "rotated bot token still authenticates launches")
            user = await login(url, token, "10001")
            other = await login(url, token, "10002")
            for secret, expected in ((old_webhook, 401), (webhook, 200)):
                response = await asyncio.to_thread(http, url, "/api/max/webhook",
                                                  body={"update_type": "qa_probe", "event_id": "qa-" + uuid4().hex},
                                                  extra_headers={"X-Max-Bot-Api-Secret": secret})
                require(response[0] == expected, "webhook credential rotation failed")
            config_status, config_body, _ = await asyncio.to_thread(http, url, "/api/omnia/config")
            require(config_status == 200 and json.loads(config_body) == updated_config,
                    "public business configuration readback mismatch")
            support_status, support_html, _ = await asyncio.to_thread(http, url, "/support", html=True)
            require(support_status == 200 and b"CANARY_SUPPORT_UPDATED" in support_html,
                    "public support page did not receive business configuration")
            await assert_records(url, user, "v1", live)
            record("public-credential-rotation-and-business-page-preserve-production-data")
            # Editable source changes are deliberately made before second publication.
            second = await accepted("v2", 3)
            await assert_records(url, user, "v1", live)
            record("source-code-and-database-edits-do-not-change-publication")
            url = await publish(second)
            await assert_records(url, user, "v2", live)
            record("compatible-republish-preserves-production-writes")
            # Remove ONLY the published application process; live DB volumes stay.
            saved = service._read(project)
            production_manager = service._production_manager(source_id)
            backend = service._backend(production_manager, production_manager.state_store.load(public_id),
                                       saved["active_release"])
            process = backend._container()
            ownership(process, allowed, project, owner)
            process.remove(force=True)
            service = CellPublicationService(settings, manager_factory=manager_for)
            outcomes = await service.reconcile()
            require(outcomes == [{"project_id": str(project), "state": "ready"}], "published process recovery failed")
            await assert_records(url, user, "v2", live)
            await assert_records(url, other, "v2", [])
            config_status, config_body, _ = await asyncio.to_thread(http, url, "/api/omnia/config")
            require(config_status == 200 and json.loads(config_body) == updated_config,
                    "recovery lost independent business configuration")
            require((await asyncio.to_thread(http, url, "/api/max/session",
                                            body={"initData": launch_data(old_token, "10001")}))[0] == 401,
                    "recovery restored revoked bot credentials")
            record("fresh-controller-and-recreated-process-retain-production-data")
            # Simulate daemon restart without stopping Docker or any other cell.
            public_containers = client.containers.list(
                all=True, filters={"label": f"omnia.workspace_id={public_id}"})
            require(bool(public_containers), "published runtime has no containers")
            for container in public_containers:
                ownership(container, allowed, project, owner)
            for container in public_containers:
                if container.status == "running":
                    container.stop(timeout=10)
            service = CellPublicationService(settings, manager_factory=manager_for)
            outcomes = await service.reconcile()
            require(outcomes == [{"project_id": str(project), "state": "ready"}],
                    "all-stopped published runtime recovery failed")
            await assert_records(url, user, "v2", live)
            await assert_records(url, other, "v2", [])
            record("all-stopped-public-runtime-recovers-with-production-data")
            # A signed owner-preview cookie must fail on the distinct public identity.
            from omnia_orchestrator.services.machine_boundary import verified_user
            require(verified_user(user.split("=", 1)[1], adapter.secret(source_id)) is None,
                    "public and private signing identities overlap")
            for invalid_cookie in (
                signed_cookie(adapter.secret(source_id), "preview"),
                signed_cookie(secrets.token_urlsafe(32), "10001"),
                signed_cookie(adapter.secret(public_id), "preview"),
            ):
                require((await asyncio.to_thread(http, url, "/api/records", cookie=invalid_cookie))[0] == 401,
                        "public API accepted preview or wrong-project cookie")
            record("public-private-session-secrets-distinct")
            # User project deletion must remove compute/ingress and release its
            # reservation without deleting retained business volumes.
            public_volumes = client.volumes.list(filters={"label": f"omnia.workspace_id={public_id}"})
            require(bool(public_volumes), "public deletion fixture has no retained volumes")
            for volume in public_volumes:
                ownership(volume, allowed, project, owner)
            await service.disable(project, slug)
            deleted = service._read(project)
            require(deleted.get("disabled") and deleted.get("deletion_completed"),
                    "public deletion lacks durable completion")
            require(production_manager._capacity_reservation_store().load(public_id) is None,
                    "public deletion retained its capacity reservation")
            require(not client.containers.list(all=True, filters={"label": f"omnia.workspace_id={public_id}"}),
                    "public deletion retained compute")
            require(not site.exists(), "public deletion retained ingress")
            for volume in public_volumes:
                ownership(client.volumes.get(volume.name), allowed, project, owner)
            service = CellPublicationService(settings, manager_factory=manager_for)
            require(await service.reconcile() == [], "deleted public runtime resurrected on recovery")
            record("public-disable-releases-compute-and-reservation-retains-volumes")
            successful = True
    except BaseException:
        # Keep detailed diagnostics only inside the private canary artifact root.
        failure = root / "failure.txt"
        failure.write_text(traceback.format_exc(), encoding="utf-8")
        failure.chmod(0o600)
        capture_failure_containers(client, root, allowed, project, owner)
        raise
    finally:
        machine_adapter.get_stack = original_stack
        if successful and args.cleanup_on_success:
            require(set(allowed) == {source_id, public_id}, "cleanup canary identity changed")
            require(host == nginx_writer.prod_host(slug) and slug.startswith("qa-publish-"), "cleanup hostname mismatch")
            inventory = []
            for kind, collection in (("container", client.containers), ("network", client.networks), ("volume", client.volumes)):
                resources = []
                for workspace in allowed:
                    options = {"filters": {"label": f"omnia.workspace_id={workspace}"}}
                    if kind == "container":
                        options["all"] = True
                    resources.extend(collection.list(**options))
                inventory.append((kind, resources))
            # Validate the entire inventory before any nginx/Docker cleanup.
            for _, resources in inventory:
                for resource in resources:
                    ownership(resource, allowed, project, owner)
            await nginx_writer.unpublish(host)
            for kind, resources in inventory:
                for resource in resources:
                    if kind == "container":
                        resource.remove(force=True)
                    elif kind == "volume":
                        resource.remove(force=False)
                    else:
                        resource.remove()
            record("canary-runtime-cleaned-private-snapshots-retained")
        client.close()
        if not successful:
            print("QA_FAILED: canary artifacts/resources retained; no automatic cleanup", flush=True)


def self_check() -> None:
    """No Docker/network activity: parse real fixture JavaScript and reject alien cleanup."""
    # Regression: a private QA ledger must not erase canonical host reservations.
    for allocated, required, reserve, total, allowed in (
        (4.2, 3.65, 2.0, 8.0, False),
        (0.0, 3.65, 2.0, 8.0, True),
        (4.2, 1.8, 2.0, 8.0, True),
    ):
        try:
            host_budget(allocated, required, reserve, total, "CPU")
        except CanaryError:
            require(not allowed, "host preflight rejected sufficient capacity")
        else:
            require(allowed, "host preflight ignored canonical reservation or host reserve")
    for name, source in fixture_files("v1").items():
        if name.endswith(".cjs"):
            result = subprocess.run(["node", "--check"], input=source, text=True, capture_output=True, timeout=10)
            require(result.returncode == 0, f"fixture JavaScript syntax failed: {name}")
    from omnia_orchestrator.core.project_machine import MachineManifest

    manifest = MachineManifest.from_files(fixture_files("v1"))
    require(manifest is not None and any(task.role == "full_build" for task in manifest.tasks),
            "fixture must declare the full_build task required by adapter.apply")
    alien = SimpleNamespace(name="omnia-cell-real", attrs={"Labels": {}}, reload=lambda: None)
    try:
        ownership(alien, {uuid4()}, uuid4(), uuid4())
    except RuntimeError:
        pass
    else:
        raise CanaryError("cleanup ownership check accepted unrelated resource")
    workspace, project, owner = uuid4(), uuid4(), uuid4()
    guard = SimpleNamespace(
        name=f"omnia-machine-test-{workspace.hex}-guard",
        attrs={"Config": {"Env": ["SECRET=must-not-escape"], "Labels": {
            "omnia.workspace_id": str(workspace), "omnia.project_id": str(project),
            "omnia.owner_id": str(owner), "omnia.namespace": "test",
            "omnia.resource_kind": "namespace-guard"}},
            "State": {"Status": "exited", "ExitCode": 137, "OOMKilled": True,
                      "Error": "must-not-escape"}},
        reload=lambda: None,
        logs=lambda **_kwargs: b"private-line must-not-escape\nPOLICY_READY=private-digest\n",
    )
    observation = container_diagnostic(guard, {workspace}, project, owner)
    require(observation["state"] == {"status": "exited", "exit_code": 137, "oom_killed": True},
            "failure capture lost container exit/OOM evidence")
    require(observation["policy_ready_count"] == 1 and observation["policy_ready"] is True,
            "failure capture lost guard policy evidence")
    require("must-not-escape" not in json.dumps(observation) and "private-digest" not in json.dumps(observation),
            "failure capture leaked environment or raw logs")
    try:
        container_diagnostic(guard, {uuid4()}, project, owner)
    except CanaryError:
        pass
    else:
        raise CanaryError("failure capture accepted unrelated container")
    same_uuid_alien = SimpleNamespace(
        name="real-user-volume",
        attrs={"Labels": {"omnia.workspace_id": str(workspace), "omnia.project_id": str(project),
                          "omnia.owner_id": str(owner), "omnia.namespace": "test"}},
        reload=lambda: None,
    )
    try:
        ownership(same_uuid_alien, {workspace}, project, owner)
    except CanaryError:
        pass
    else:
        raise CanaryError("cleanup accepted non-canary name with matching UUID")
    print("SELF_CHECK_PASS: fixture syntax, host budget, ownership denial, sanitized failure diagnostics")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="Run real isolated Docker/nginx acceptance")
    parser.add_argument("--self-check", action="store_true", help="Only validate fixtures/cleanup guard locally")
    parser.add_argument("--qa-parent")
    parser.add_argument("--core-image")
    parser.add_argument("--compact-qa-resources", action="store_true")
    parser.add_argument("--other-qa-cpu-cores", type=float, default=0,
                        help="Reviewed concurrent non-canonical QA CPU caps; never subtracts host reserve")
    retention = parser.add_mutually_exclusive_group()
    retention.add_argument("--cleanup-on-success", action="store_true")
    retention.add_argument("--keep", action="store_true", help="Retain canary resources and private root (default)")
    parser.add_argument("--deadline-seconds", type=int, default=2700)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if not args.run or not args.qa_parent or not args.core_image:
        parser.error("--run, --qa-parent and immutable --core-image are required")
    if not 300 <= args.deadline_seconds <= 5400:
        parser.error("--deadline-seconds must be between 300 and 5400")
    if args.other_qa_cpu_cores < 0:
        parser.error("--other-qa-cpu-cores must be nonnegative")
    try:
        asyncio.run(run(args))
    except Exception as error:
        # Docker exceptions can include environment values; never print raw error/repr.
        detail = str(error) if isinstance(error, CanaryError) else type(error).__name__
        print("QA_FAILED: " + detail + "; inspect retained private QA artifacts", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
