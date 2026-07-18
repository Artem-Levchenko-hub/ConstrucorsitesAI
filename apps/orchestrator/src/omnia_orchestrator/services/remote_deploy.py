"""Удалённый деплой собранного образа на чужой VPS (BYO-VPS).

Механика (проверена вживую на реальном SSH-пути):
  1. `docker save <tag>` ЛОКАЛЬНО → пайп в `ssh <target> docker load` — переносим
     готовый prod-образ на машину пользователя (без приватного реестра).
  2. `ssh <target> docker run -d ...` — запускаем контейнер на его машине.
  3. `ssh <target> curl ...` — проверяем, что приложение отвечает.

Секреты идут через core.ssh.ssh_prefix (ключ во временном файле / пароль в env),
в argv/логи не попадают. Локальный деплой (services/builder.py) этим модулем НЕ
затрагивается — он только для проектов с выбранной чужой целью.

НЕ входит сюда (следующий шаг, нужен reachable foreign VPS для доводки):
edge/nginx + TLS на удалённой машине и Postgres для fullstack — пока возвращаем
прямой `http://<host>:<port>`; домен/SSL подключаются отдельно.
"""

from __future__ import annotations

import asyncio
import os

import structlog

from omnia_orchestrator.core import ssh
from omnia_orchestrator.core.shell import run

log = structlog.get_logger("omnia_orchestrator.remote_deploy")


async def _save_load(
    image_tag: str, prefix: list[str], env: dict[str, str] | None
) -> tuple[bool, str]:
    """`docker save <tag>` | `ssh … docker load` без shell (два процесса, пайп).

    Процессы соединяются НАСТОЯЩИМ OS-пайпом (os.pipe), а не через shell — так
    нет инъекции из host/user, а секрет остаётся в env/файле ключа, не в argv.
    Возвращает (ok, detail).
    """
    read_fd, write_fd = os.pipe()
    try:
        save = await asyncio.create_subprocess_exec(
            "docker", "save", image_tag,
            stdout=write_fd, stderr=asyncio.subprocess.PIPE,
        )
        os.close(write_fd)  # родитель отдал write-конец процессу save
        write_fd = -1
        loader = await asyncio.create_subprocess_exec(
            *prefix, "docker load",
            stdin=read_fd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env,
        )
        os.close(read_fd)  # родитель отдал read-конец процессу loader
        read_fd = -1
        try:
            out_b, err_b = await asyncio.wait_for(loader.communicate(), timeout=600)
        except TimeoutError:
            loader.kill()
            save.kill()
            return False, "Таймаут переноса образа (>600с)."
        await save.wait()
    finally:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)
    out = (out_b or b"").decode("utf-8", "replace")
    err = (err_b or b"").decode("utf-8", "replace")
    if loader.returncode == 0 and "Loaded image" in out:
        return True, out.strip().splitlines()[-1]
    if save.returncode not in (0, None):
        return False, f"docker save упал: rc={save.returncode}"
    tail = err.strip()[-200:] or out.strip()[-200:]
    return False, f"docker load на сервере упал: {tail}"


async def deploy_to_target(
    *,
    creds: dict[str, object],
    image_tag: str,
    slug: str,
    host_port: int,
    container_port: int = 3000,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Развернуть образ `image_tag` на чужом VPS. Возвращает {ok, url, detail}.

    `creds` = {host, port, user, auth_type, secret}. `env` — переменные
    окружения контейнера (для static-проекта минимальные; DATABASE_URL для
    fullstack пока ожидает БД на той же машине — вне этого шага).
    """
    host = str(creds["host"])
    try:
        prefix, ssh_env, cleanup = ssh.ssh_prefix(
            host=host, port=int(str(creds["port"])), user=str(creds["user"]),
            auth_type=str(creds["auth_type"]), secret=str(creds["secret"]),
        )
    except OSError as exc:
        return {"ok": False, "url": None, "detail": f"Ошибка подготовки ключа: {exc}"}

    name = f"omnia-app-{slug}"
    try:
        # 1. Перенос образа.
        log.info("remote_deploy.transfer", host=host, tag=image_tag)
        ok, detail = await _save_load(image_tag, prefix, ssh_env)
        if not ok:
            return {"ok": False, "url": None, "detail": f"Перенос образа не удался: {detail}"}

        # 2. Снести прошлый контейнер и запустить новый.
        try:
            await run([*prefix, f"docker rm -f {name}"], timeout=30, env=ssh_env)
        except FileNotFoundError:
            return {
                "ok": False, "url": None,
                "detail": "На сервере Omnia нет sshpass — вход по паролю недоступен, "
                          "используйте ключ.",
            }
        env_args = ""
        for k, v in (env or {}).items():
            # Значения окружения — наши (не пользовательский ввод); экранируем кавычку.
            safe = str(v).replace("'", "'\\''")
            env_args += f" -e {k}='{safe}'"
        runcmd = (
            f"docker run -d --name {name} --restart unless-stopped "
            f"-p {host_port}:{container_port}{env_args} {image_tag}"
        )
        rr = await run([*prefix, runcmd], timeout=60, env=ssh_env)
        if rr.rc != 0:
            return {"ok": False, "url": None,
                    "detail": f"Запуск контейнера не удался: {rr.stderr.strip()[-200:]}"}

        # 3. Health-check с самой машины (даём приложению подняться).
        await asyncio.sleep(3)
        health = await run(
            [*prefix,
             f"curl -s -o /dev/null -w %{{http_code}} --max-time 8 http://127.0.0.1:{host_port}/"],
            timeout=20, env=ssh_env,
        )
        code = (health.stdout or "").strip()
        url = f"http://{host}:{host_port}"
        if code and code[0] in "23":
            return {
                "ok": True, "url": url,
                "detail": f"Развёрнуто на вашем сервере (HTTP {code}). {detail}",
            }
        # Контейнер запущен, но ещё не отвечает — не считаем фаталом.
        status = await run(
            [*prefix, f"docker ps --filter name={name} --format {{{{.Status}}}}"],
            timeout=20, env=ssh_env,
        )
        code_txt = code or "нет ответа"
        if status.stdout.strip().startswith("Up"):
            return {
                "ok": True, "url": url,
                "detail": f"Контейнер запущён на вашем сервере, приложение "
                          f"прогревается (HTTP {code_txt}).",
            }
        return {
            "ok": False, "url": url,
            "detail": f"Контейнер не поднялся (HTTP {code_txt}).",
        }
    finally:
        cleanup()
