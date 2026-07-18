"""SSH к чужому VPS (BYO-VPS) — через СИСТЕМНЫЙ ssh, без Python-зависимости.

Оркестратор работает на хосте как systemd-сервис, поэтому `ssh`, `ssh-keyscan`
и (для пароля) `sshpass` доступны напрямую. Секреты НЕ попадают в argv (а значит
и в логи shell.run): приватный ключ пишется во временный файл с правами 0600,
пароль передаётся в переменной окружения `SSHPASS` для `sshpass -e`.

Здесь: (1) проверка VPS (verify_target — зайти + проверить Docker), (2) сборка
префикса ssh-команды для повторного использования (_ssh_prefix) — на нём же
строится реальный удалённый деплой (services/remote_deploy.py).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable

from omnia_orchestrator.core.shell import run

# Проверочная команда на удалённой машине: версия Docker-демона (или маркер, что
# докера нет). `2>/dev/null || echo` — чтобы отсутствие докера не ломало ssh rc.
_REMOTE_PROBE = "docker version --format '{{.Server.Version}}' 2>/dev/null || echo NO_DOCKER"

_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=12",
    "-o", "NumberOfPasswordPrompts=1",
]


async def host_key(host: str, port: int) -> str | None:
    """Снять host-key сервера (для пиннинга от MITM). Best-effort."""
    scan = await run(
        ["ssh-keyscan", "-p", str(port), "-t", "ed25519,rsa,ecdsa", host], timeout=15
    )
    line = (scan.stdout or "").strip().splitlines()
    return line[0] if line else None


def ssh_prefix(
    *, host: str, port: int, user: str, auth_type: str, secret: str
) -> tuple[list[str], dict[str, str] | None, Callable[[], None]]:
    """Собрать префикс ssh-команды до `user@host` включительно + env + cleanup.

    Возвращает (argv, env, cleanup): `argv` готов к добавлению удалённой команды
    последним аргументом; `env` — окружение для процесса (для пароля несёт
    SSHPASS); `cleanup()` удаляет временный файл ключа (вызвать после запуска).
    Секрет никогда не попадает в argv.
    """
    target = f"{user}@{host}"
    if auth_type == "key":
        fd, keypath = tempfile.mkstemp(prefix="omnia-ssh-key-")
        os.write(fd, secret.encode("utf-8"))
        if not secret.endswith("\n"):
            os.write(fd, b"\n")
        os.close(fd)
        os.chmod(keypath, 0o600)

        def _cleanup() -> None:
            try:
                os.unlink(keypath)
            except OSError:
                pass

        argv = [
            "ssh", "-i", keypath,
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-p", str(port), *_SSH_OPTS, target,
        ]
        return argv, None, _cleanup

    # Пароль: sshpass -e берёт его из env SSHPASS (не из argv → не в логах).
    argv = [
        "sshpass", "-e", "ssh",
        "-o", "PubkeyAuthentication=no",
        "-p", str(port), *_SSH_OPTS, target,
    ]
    env = {**os.environ, "SSHPASS": secret}
    return argv, env, (lambda: None)


def _interpret(rc: int, stdout: str, stderr: str) -> dict[str, object]:
    out = (stdout or "").strip()
    err = (stderr or "")
    if rc == 0 and out and out != "NO_DOCKER":
        return {
            "ok": True,
            "detail": f"Подключение успешно, Docker {out} на сервере.",
            "docker_ok": True,
            "docker_version": out,
        }
    if rc == 0 and out == "NO_DOCKER":
        return {
            "ok": True,
            "detail": "Подключение успешно, но на сервере нет Docker — установите его для деплоя.",
            "docker_ok": False,
            "docker_version": None,
        }
    low = err.lower()
    if "permission denied" in low:
        reason = "Отказ в доступе — проверьте пользователя, ключ или пароль."
    elif "connection refused" in low:
        reason = "Соединение отклонено — проверьте host и порт SSH."
    elif "could not resolve" in low or "name or service not known" in low:
        reason = "Не удалось разрешить host — проверьте адрес сервера."
    elif "connection timed out" in low or "timed out" in low:
        reason = "Таймаут подключения — сервер недоступен или закрыт фаервол."
    else:
        reason = f"Не удалось подключиться: {err.strip()[-200:] or 'неизвестная ошибка'}"
    return {"ok": False, "detail": reason, "docker_ok": False, "docker_version": None}


async def verify_target(
    *, host: str, port: int, user: str, auth_type: str, secret: str
) -> dict[str, object]:
    """Зайти на VPS пользователя и проверить наличие Docker.

    Возвращает {ok, detail, docker_ok, docker_version, host_key}. Никогда не
    бросает из-за неверных кредов — только описывает результат.
    """
    hk = await host_key(host, port)
    try:
        prefix, env, cleanup = ssh_prefix(
            host=host, port=port, user=user, auth_type=auth_type, secret=secret
        )
    except OSError as exc:
        return {"ok": False, "detail": f"Ошибка подготовки ключа: {exc}",
                "docker_ok": False, "docker_version": None, "host_key": hk}
    try:
        res = await run([*prefix, _REMOTE_PROBE], timeout=35, env=env)
    except FileNotFoundError:
        cleanup()
        return {
            "ok": False,
            "detail": "На сервере Omnia не установлен sshpass — используйте вход по SSH-ключу.",
            "docker_ok": False, "docker_version": None, "host_key": hk,
        }
    finally:
        cleanup()
    result = _interpret(res.rc, res.stdout, res.stderr)
    result["host_key"] = hk
    return result
