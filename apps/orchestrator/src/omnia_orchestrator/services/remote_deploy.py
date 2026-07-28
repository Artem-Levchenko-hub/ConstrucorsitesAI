"""Transactional blue/green deployment to an owner-managed VPS."""

from __future__ import annotations

import asyncio
import base64
import re
import secrets
import zlib
from collections.abc import Callable

import structlog

from omnia_orchestrator.core import ssh

log = structlog.get_logger("omnia_orchestrator.remote_deploy")

POSTGRES_IMAGE = "postgres:16-alpine"
_EDGE_NAME = "omnia-edge"
_ROOT = "$HOME/.omnia"
_IDENT = re.compile(r"[^a-zA-Z0-9_.-]")


def _ident(value: str, *, limit: int = 48) -> str:
    return _IDENT.sub("-", value).strip("-")[:limit] or "project"


def _caddyfile(domains: list[str], app_port: int, public_port: int | None = None) -> str:
    """Per-project route; the global Caddyfile imports all of these files."""
    address = ", ".join(domains) if domains else f"http://:{public_port}"
    return f"{address} {{\n\treverse_proxy 127.0.0.1:{app_port}\n}}\n"


async def _write_file(
    session: ssh.SSHSession, path: str, content: str, *, mode: str = "600"
) -> None:
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    result = await session.run(
        f"umask 077; mkdir -p {_ROOT}/env {_ROOT}/caddy/routes; "
        f"base64 -d > {path}; chmod {mode} {path}",
        input_data=encoded,
        timeout=30,
    )
    if not result.ok:
        raise RuntimeError(f"Не удалось записать runtime-файл: {result.stderr[-180:]}")


async def _save_load(
    image_tag: str,
    session: ssh.SSHSession,
    progress: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Compress docker-save locally and stream it through the pinned SSH channel."""
    save = await asyncio.create_subprocess_exec(
        "docker",
        "save",
        image_tag,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert save.stdout is not None
    remote = await session.connection.create_process("gzip -d | docker load", encoding=None)
    transferred = 0
    compressor = zlib.compressobj(level=1, wbits=31)
    try:
        while chunk := await save.stdout.read(1024 * 1024):
            compressed = compressor.compress(chunk)
            if compressed:
                remote.stdin.write(compressed)
                await remote.stdin.drain()
                transferred += len(compressed)
            if progress and transferred % (20 * 1024 * 1024) < len(compressed):
                progress(f"Передано {transferred // (1024 * 1024)} МБ образа")
        final_chunk = compressor.flush()
        if final_chunk:
            remote.stdin.write(final_chunk)
            await remote.stdin.drain()
            transferred += len(final_chunk)
        remote.stdin.write_eof()
        remote_out, remote_err = await asyncio.wait_for(remote.communicate(), timeout=900)
        await save.wait()
        assert save.stderr is not None
        save_err = await save.stderr.read()
    except (TimeoutError, asyncio.CancelledError):
        save.kill()
        remote.abort()
        raise
    out = (remote_out or b"").decode("utf-8", "replace")
    err = (remote_err or b"").decode("utf-8", "replace")
    if save.returncode != 0:
        return False, f"docker save: {(save_err or b'').decode('utf-8', 'replace')[-180:]}"
    if remote.exit_status != 0:
        return False, f"docker load: {(err or out)[-220:]}"
    return True, out.strip().splitlines()[-1] if out.strip() else "образ загружен"


async def _ensure_database(
    session: ssh.SSHSession, project_key: str, network: str
) -> tuple[str, str, bool]:
    db_name = f"omnia-db-{project_key}"
    volume = f"omnia-db-data-{project_key}"
    db_env = f"{_ROOT}/env/{project_key}.db.env"
    exists = await session.run(f"test -s {db_env}", timeout=10)
    if not exists.ok:
        password = secrets.token_urlsafe(32)
        await _write_file(
            session,
            db_env,
            f"POSTGRES_USER=omnia\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=omnia\n",
        )
    volume_state = await session.run(f"docker volume inspect {volume} >/dev/null 2>&1")
    fresh_volume = not volume_state.ok
    await session.run(f"docker network create {network} >/dev/null 2>&1 || true")
    running = await session.run(
        f"docker inspect -f '{{{{.State.Running}}}}' {db_name} 2>/dev/null || true"
    )
    if running.stdout.strip() != "true":
        await session.run(f"docker rm -f {db_name} >/dev/null 2>&1 || true")
        started = await session.run(
            f"docker run -d --name {db_name} --restart unless-stopped "
            f"--label omnia.managed=true --label omnia.project={project_key} "
            f"--network {network} --network-alias db --env-file {db_env} "
            f"-v {volume}:/var/lib/postgresql/data {POSTGRES_IMAGE}",
            timeout=180,
        )
        if not started.ok:
            raise RuntimeError(f"Postgres не запустился: {started.stderr[-220:]}")
    for _ in range(40):
        ready = await session.run(f"docker exec {db_name} pg_isready -U omnia", timeout=10)
        if ready.ok:
            return db_name, db_env, fresh_volume
        await asyncio.sleep(1.5)
    raise RuntimeError("Postgres не стал готов за 60 секунд.")


async def _ensure_edge(session: ssh.SSHSession) -> None:
    await _write_file(
        session,
        f"{_ROOT}/caddy/Caddyfile",
        "import /config/routes/*.caddy\n",
        mode="644",
    )
    # Caddy treats an import glob without matches as invalid. Keep one inert
    # route so the first project can bootstrap the shared edge safely.
    await _write_file(
        session,
        f"{_ROOT}/caddy/routes/00-empty.caddy",
        "http://127.0.0.1:65535 {\n  respond 404\n}\n",
        mode="644",
    )
    status = await session.run(
        f"docker inspect -f '{{{{.State.Running}}}}' {_EDGE_NAME} 2>/dev/null || true"
    )
    if status.stdout.strip() == "true":
        return
    await session.run(f"docker rm -f {_EDGE_NAME} >/dev/null 2>&1 || true")
    result = await session.run(
        f"docker run -d --name {_EDGE_NAME} --restart unless-stopped "
        f"--label omnia.managed=true --network host "
        f"-v {_ROOT}/caddy:/config -v omnia-caddy-data:/data "
        f"caddy:2 caddy run --config /config/Caddyfile --adapter caddyfile",
        timeout=180,
    )
    if not result.ok:
        raise RuntimeError(f"Caddy не запустился: {result.stderr[-220:]}")


async def _health(session: ssh.SSHSession, port: int) -> bool:
    for _ in range(60):
        result = await session.run(
            f"curl -fsS --max-time 4 http://127.0.0.1:{port}/ >/dev/null", timeout=8
        )
        if result.ok:
            return True
        await asyncio.sleep(2)
    return False


async def deploy_to_target(
    *,
    creds: dict[str, object],
    image_tag: str,
    project_id: str,
    run_id: str,
    slug: str,
    host_port: int,
    container_port: int = 3000,
    env: dict[str, str] | None = None,
    domains: list[str] | None = None,
    needs_database: bool = True,
    db_dump: str | None = None,
    db_schema: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Start a candidate, check it, atomically swap its route, then prune old."""
    project_key = _ident(project_id.replace("-", ""), limit=20)
    run_key = _ident(run_id.replace("-", ""), limit=12)
    name = f"omnia-app-{project_key}-{run_key}"
    network = f"omnia-net-{project_key}"
    env_path = f"{_ROOT}/env/{project_key}-{run_key}.env"
    session: ssh.SSHSession | None = None
    candidate_started = False
    database_created = False
    db_name: str | None = None
    try:
        session = await ssh.connect(
            resolved_ip=str(creds["resolved_ip"]),
            port=int(str(creds["port"])),
            user=str(creds["user"]),
            auth_type=str(creds["auth_type"]),
            secret=str(creds["secret"]),
            known_host_key=str(creds["known_host_key"]),
        )
        if progress:
            progress("Защищённое SSH-соединение установлено")
        ok, transfer_detail = await _save_load(image_tag, session, progress)
        if not ok:
            raise RuntimeError(f"Перенос образа не удался: {transfer_detail}")

        image_arch = await session.run(
            f"docker image inspect {image_tag} --format '{{{{.Architecture}}}}'", timeout=20
        )
        host_arch = await session.run("uname -m", timeout=10)
        normalized = {"x86_64": "amd64", "aarch64": "arm64"}
        remote_arch = normalized.get(host_arch.stdout.strip(), host_arch.stdout.strip())
        if remote_arch != image_arch.stdout.strip():
            raise RuntimeError(
                f"Архитектура образа {image_arch.stdout.strip()} не совпадает "
                f"с сервером {host_arch.stdout.strip()}."
            )

        db_env: str | None = None
        if needs_database:
            db_name, db_env, database_created = await _ensure_database(
                session, project_key, network
            )
            if database_created and db_dump:
                restored = await session.run(
                    f"docker exec -i {db_name} psql -v ON_ERROR_STOP=1 -U omnia -d omnia",
                    input_data=db_dump,
                    timeout=300,
                )
                if not restored.ok:
                    raise RuntimeError(f"Миграция данных не удалась: {restored.stderr[-300:]}")
        else:
            await session.run(f"docker network create {network} >/dev/null 2>&1 || true")

        env_text = "".join(f"{key}={value}\n" for key, value in (env or {}).items())
        await _write_file(session, env_path, env_text)
        if db_env:
            merged = await session.run(
                f"set -eu; . {db_env}; "
                f"printf 'DATABASE_URL=postgresql://omnia:%s@db:5432/omnia"
                + (f"?options=-c%%20search_path%%3D{db_schema}" if db_schema else "")
                + "\\n' "
                f'"$POSTGRES_PASSWORD" >> {env_path}',
                timeout=15,
            )
            if not merged.ok:
                raise RuntimeError("Не удалось подготовить подключение к Postgres.")

        started = await session.run(
            f"docker run -d --name {name} --restart unless-stopped "
            f"--label omnia.managed=true --label omnia.project={project_key} "
            f"--label omnia.kind=app --label omnia.run={run_key} "
            f"--network {network} --env-file {env_path} "
            f"-p 127.0.0.1::{container_port} {image_tag}",
            timeout=90,
        )
        if not started.ok:
            raise RuntimeError(f"Новый контейнер не запустился: {started.stderr[-220:]}")
        candidate_started = True
        port_result = await session.run(
            f"docker port {name} {container_port}/tcp | head -1 | awk -F: '{{print $NF}}'"
        )
        try:
            candidate_port = int(port_result.stdout.strip())
        except ValueError as exc:
            raise RuntimeError("Docker не вернул порт нового контейнера.") from exc
        if progress:
            progress("Новый контейнер запущен, проверяем приложение")
        if not await _health(session, candidate_port):
            logs = await session.run(f"docker logs --tail 80 {name}", timeout=20)
            raise RuntimeError(
                "Новая версия не прошла health-check: " + (logs.stderr or logs.stdout)[-500:]
            )

        await _ensure_edge(session)
        route = _caddyfile(domains or [], candidate_port, host_port)
        route_path = f"{_ROOT}/caddy/routes/{project_key}.caddy"
        previous_route = await session.run(
            f"base64 -w0 {route_path} 2>/dev/null || true", timeout=10
        )
        await _write_file(session, route_path, route, mode="644")
        valid = await session.run(
            f"docker exec {_EDGE_NAME} caddy validate --config /config/Caddyfile "
            f"--adapter caddyfile",
            timeout=30,
        )
        if not valid.ok:
            if previous_route.stdout.strip():
                await _write_file(
                    session,
                    route_path,
                    base64.b64decode(previous_route.stdout.strip()).decode("utf-8"),
                    mode="644",
                )
            else:
                await session.run(f"rm -f {route_path}")
            raise RuntimeError(f"Конфигурация домена некорректна: {valid.stderr[-220:]}")
        reloaded = await session.run(
            f"docker exec {_EDGE_NAME} caddy reload --config /config/Caddyfile --adapter caddyfile",
            timeout=30,
        )
        if not reloaded.ok:
            if previous_route.stdout.strip():
                await _write_file(
                    session,
                    route_path,
                    base64.b64decode(previous_route.stdout.strip()).decode("utf-8"),
                    mode="644",
                )
            else:
                await session.run(f"rm -f {route_path}")
            await session.run(
                f"docker exec {_EDGE_NAME} caddy reload --config /config/Caddyfile "
                f"--adapter caddyfile >/dev/null 2>&1 || true"
            )
            raise RuntimeError(f"Caddy не переключил трафик: {reloaded.stderr[-220:]}")

        old = await session.run(
            f"docker ps -aq --no-trunc --filter label=omnia.project={project_key} "
            f"--filter label=omnia.kind=app"
        )
        for container_id in old.stdout.split():
            if container_id != started.stdout.strip():
                await session.run(f"docker rm -f {container_id}", timeout=30)
        await session.run(
            f"find {_ROOT}/env -maxdepth 1 -type f -name '{project_key}-*.env' "
            f"! -name '{project_key}-{run_key}.env' -delete",
            timeout=30,
        )
        safe_slug = _ident(slug)
        await session.run(
            f"docker images 'omnia-app-{safe_slug}' --format '{{{{.ID}}}}' "
            f"| awk 'NR>3 {{print $1}}' | xargs -r docker rmi "
            f">/dev/null 2>&1 || true"
        )
        url = (
            f"https://{(domains or [])[0]}"
            if domains
            else f"http://{creds['resolved_ip']}:{host_port}"
        )
        return {
            "ok": True,
            "url": url,
            "detail": "Новая версия проверена и трафик переключён без простоя.",
        }
    except asyncio.CancelledError:
        if session is not None and candidate_started:
            await session.run(f"docker rm -f {name} >/dev/null 2>&1 || true", timeout=30)
        if session is not None and database_created and db_name:
            await session.run(
                f"docker rm -f {db_name} >/dev/null 2>&1 || true; "
                f"docker volume rm omnia-db-data-{project_key} >/dev/null 2>&1 || true",
                timeout=60,
            )
        raise
    except Exception as exc:
        log.warning("remote_deploy.failed", project_id=project_id, err=str(exc))
        if session is not None and candidate_started:
            await session.run(f"docker rm -f {name} >/dev/null 2>&1 || true", timeout=30)
        if session is not None and database_created and db_name:
            await session.run(
                f"docker rm -f {db_name} >/dev/null 2>&1 || true; "
                f"docker volume rm omnia-db-data-{project_key} >/dev/null 2>&1 || true",
                timeout=60,
            )
        return {"ok": False, "url": None, "detail": str(exc)[:800]}
    finally:
        if session is not None:
            await session.close()


async def teardown_target(*, creds: dict[str, object], project_id: str) -> dict[str, object]:
    """Remove one project's containers/routes/volume without touching neighbours."""
    project_key = _ident(project_id.replace("-", ""), limit=20)
    session = await ssh.connect(
        resolved_ip=str(creds["resolved_ip"]),
        port=int(str(creds["port"])),
        user=str(creds["user"]),
        auth_type=str(creds["auth_type"]),
        secret=str(creds["secret"]),
        known_host_key=str(creds["known_host_key"]),
    )
    try:
        await session.run(
            f"ids=$(docker ps -aq --filter label=omnia.project={project_key}); "
            f'test -z "$ids" || docker rm -f $ids; '
            f"docker network rm omnia-net-{project_key} >/dev/null 2>&1 || true; "
            f"docker volume rm omnia-db-data-{project_key} >/dev/null 2>&1 || true; "
            f"rm -f {_ROOT}/caddy/routes/{project_key}.caddy {_ROOT}/env/{project_key}*.env; "
            f"docker exec {_EDGE_NAME} caddy reload --config /config/Caddyfile "
            f"--adapter caddyfile >/dev/null 2>&1 || true",
            timeout=120,
        )
        return {"ok": True, "detail": "Удалённый runtime проекта удалён."}
    finally:
        await session.close()


async def target_logs(
    *, creds: dict[str, object], project_id: str, tail: int = 200
) -> dict[str, object]:
    project_key = _ident(project_id.replace("-", ""), limit=20)
    session = await ssh.connect(
        resolved_ip=str(creds["resolved_ip"]),
        port=int(str(creds["port"])),
        user=str(creds["user"]),
        auth_type=str(creds["auth_type"]),
        secret=str(creds["secret"]),
        known_host_key=str(creds["known_host_key"]),
    )
    try:
        result = await session.run(
            f"id=$(docker ps -q --filter label=omnia.project={project_key} "
            f"--filter label=omnia.kind=app | head -1); "
            f'test -z "$id" || docker logs --tail {max(1, min(tail, 2000))} "$id"',
            timeout=30,
        )
        return {"ok": result.ok, "logs": (result.stdout + result.stderr)[-100_000:]}
    finally:
        await session.close()


async def sync_routes(
    *,
    creds: dict[str, object],
    project_id: str,
    domains: list[str],
    host_port: int,
) -> dict[str, object]:
    """Rebuild one project's Caddy route after its domain set changes."""
    project_key = _ident(project_id.replace("-", ""), limit=20)
    session = await ssh.connect(
        resolved_ip=str(creds["resolved_ip"]),
        port=int(str(creds["port"])),
        user=str(creds["user"]),
        auth_type=str(creds["auth_type"]),
        secret=str(creds["secret"]),
        known_host_key=str(creds["known_host_key"]),
    )
    try:
        container = await session.run(
            f"docker ps -q --filter label=omnia.project={project_key} "
            f"--filter label=omnia.kind=app | head -1"
        )
        if not container.stdout.strip():
            return {"ok": True, "detail": "У проекта пока нет удалённого runtime."}
        port = await session.run(
            f"docker port {container.stdout.strip()} 3000/tcp | head -1 | awk -F: '{{print $NF}}'"
        )
        candidate_port = int(port.stdout.strip())
        await _ensure_edge(session)
        route_path = f"{_ROOT}/caddy/routes/{project_key}.caddy"
        previous_route = await session.run(
            f"base64 -w0 {route_path} 2>/dev/null || true", timeout=10
        )
        await _write_file(
            session,
            route_path,
            _caddyfile(domains, candidate_port, host_port),
            mode="644",
        )
        valid = await session.run(
            f"docker exec {_EDGE_NAME} caddy validate --config /config/Caddyfile "
            f"--adapter caddyfile",
            timeout=30,
        )
        if not valid.ok:
            if previous_route.stdout.strip():
                await _write_file(
                    session,
                    route_path,
                    base64.b64decode(previous_route.stdout.strip()).decode("utf-8"),
                    mode="644",
                )
            else:
                await session.run(f"rm -f {route_path}")
            raise RuntimeError(f"Caddy не принял маршруты: {valid.stderr[-220:]}")
        result = await session.run(
            f"docker exec {_EDGE_NAME} caddy reload --config /config/Caddyfile --adapter caddyfile",
            timeout=30,
        )
        if not result.ok:
            if previous_route.stdout.strip():
                await _write_file(
                    session,
                    route_path,
                    base64.b64decode(previous_route.stdout.strip()).decode("utf-8"),
                    mode="644",
                )
                await session.run(
                    f"docker exec {_EDGE_NAME} caddy reload --config /config/Caddyfile "
                    f"--adapter caddyfile >/dev/null 2>&1 || true"
                )
            else:
                await session.run(f"rm -f {route_path}")
            raise RuntimeError(f"Caddy не обновил маршруты: {result.stderr[-220:]}")
        return {"ok": True, "detail": "Маршруты доменов обновлены."}
    finally:
        await session.close()
