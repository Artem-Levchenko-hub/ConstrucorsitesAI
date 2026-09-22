"""Восстановление после оборвавшегося отката: сначала смотрим, потом трогаем.

Порядок здесь — это и есть защита. Инспекция ничего не запускает и никуда не пишет;
свидетель снимается с копии, а оригинальный том монтируется только на чтение и не
запускается никогда. Если что-то не сошлось с намерением — остановка, а не
«похоже, это оно».

Все создаваемые ресурсы носят префикс ``omnia-recovery-`` и удаляются в ``finally``.
Ничего без этого префикса не останавливается и не удаляется — на это есть тест.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from omnia_orchestrator.schemas.restoration_recovery import (
    RecoveryFinding,
    RecoveryPhase,
    RecoveryReport,
    RestorationRecoveryIntent,
)

SCRATCH_PREFIX = "omnia-recovery-"
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,127}$")
# У базы приложения том смонтирован прямо на этот путь: том и есть PGDATA.
_PGDATA = "/var/lib/postgresql/data"
_SOCKET = "/tmp"


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class DockerRunner(Protocol):
    """Единственный путь наружу; в тестах подменяется записывающим двойником."""

    def run(self, args: Sequence[str], *, timeout: float = 600.0) -> CommandResult: ...


@dataclass
class ScratchPool:
    """Ресурсы этого прогона по точным именам; всё удаляется в ``finally``."""

    runner: DockerRunner
    containers: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)

    def name(self, kind: str) -> str:
        value = SCRATCH_PREFIX + secrets.token_hex(12)
        (self.containers if kind == "container" else self.volumes).append(value)
        return value

    def release(self) -> None:
        for name in reversed(self.containers):
            _require_scratch(name)
            self.runner.run(["rm", "--force", "--volumes", name], timeout=120)
        for name in reversed(self.volumes):
            _require_scratch(name)
            self.runner.run(["volume", "rm", "--force", name], timeout=120)
        self.containers.clear()
        self.volumes.clear()


def _require_scratch(name: str) -> str:
    if not name.startswith(SCRATCH_PREFIX) or _NAME_RE.fullmatch(name) is None:
        raise ValueError(f"refusing to touch a resource outside this recovery: {name!r}")
    return name


def _finding(check: str, ok: bool, detail: str = "") -> RecoveryFinding:
    return RecoveryFinding(check=check, ok=ok, detail=detail[:240])


def _report(
    phase: RecoveryPhase,
    findings: Sequence[RecoveryFinding],
    witness_digest: str | None = None,
) -> RecoveryReport:
    return RecoveryReport(
        phase=phase,
        ok=all(item.ok for item in findings),
        findings=tuple(findings),
        witness_digest=witness_digest,
    )


def _inspect_volume(runner: DockerRunner, name: str) -> Mapping[str, object] | None:
    result = runner.run(["volume", "inspect", name, "--format", "{{json .}}"], timeout=60)
    if not result.ok:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def inspect_recovery(
    intent: RestorationRecoveryIntent,
    *,
    runner: DockerRunner,
    registry: Mapping[str, object],
) -> RecoveryReport:
    """Сверить намерение с тем, что реально есть. Ничего не запускает и не пишет.

    ``registry`` — запись о машине проекта: именно она, а не форма имени, называет
    активные тома. Совпадение префикса доказательством принадлежности не считается.
    """
    findings: list[RecoveryFinding] = []

    active_db = registry.get("active_database_volume")
    active_code = registry.get("active_code_volume")
    findings.append(
        _finding(
            "registry_database_binding",
            active_db == intent.active_database_volume,
            "registry names a different active database volume"
            if active_db != intent.active_database_volume
            else "",
        )
    )
    findings.append(
        _finding(
            "registry_code_binding",
            active_code == intent.active_code_volume,
            "registry names a different active code volume"
            if active_code != intent.active_code_volume
            else "",
        )
    )

    workspace = registry.get("workspace_id")
    findings.append(
        _finding(
            "workspace_ownership",
            str(workspace) == str(intent.workspace_id),
            "registry belongs to another workspace" if workspace else "registry has no workspace",
        )
    )

    for label, name in (
        ("database_volume_present", intent.active_database_volume),
        ("code_volume_present", intent.active_code_volume),
    ):
        details = _inspect_volume(runner, name)
        findings.append(_finding(label, details is not None, "" if details else "volume not found"))

    holders = runner.run(
        ["ps", "--filter", f"volume={intent.active_database_volume}", "--format", "{{.ID}}"],
        timeout=60,
    )
    findings.append(
        _finding(
            "database_volume_detached",
            holders.ok and not holders.stdout.strip(),
            "a running container still holds the original database volume"
            if holders.stdout.strip()
            else "",
        )
    )
    return _report("inspect", findings)


def clone_witness(
    intent: RestorationRecoveryIntent,
    *,
    runner: DockerRunner,
    postgres_image: str,
    helper_image: str,
    witness_sql: Sequence[str],
) -> RecoveryReport:
    """Снять независимого SQL-свидетеля с одноразовой копии оригинального тома.

    Оригинал монтируется ``:ro`` и не запускается: PostgreSQL поднимается только на
    копии, поэтому восстановление журнала пишет исключительно в неё.
    """
    findings: list[RecoveryFinding] = []
    pool = ScratchPool(runner)
    try:
        holders = runner.run(
            ["ps", "--filter", f"volume={intent.active_database_volume}", "--format", "{{.ID}}"],
            timeout=60,
        )
        if holders.stdout.strip():
            return _report(
                "clone_witness",
                [_finding("database_volume_detached", False, "the original volume is in use")],
            )

        clone = pool.name("volume")
        created = runner.run(["volume", "create", clone], timeout=60)
        if not created.ok:
            return _report(
                "clone_witness", [_finding("scratch_volume", False, "scratch volume not created")]
            )

        copied = runner.run(
            [
                "run", "--rm", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE", "--cap-add", "CHOWN",
                "--cap-add", "FOWNER",
                "--volume", f"{intent.active_database_volume}:/source:ro",
                "--volume", f"{clone}:/target",
                "--entrypoint", "sh", helper_image,
                "-ec", "cd /source && test -f PG_VERSION && tar -cf - . | tar -xf - -C /target"
                " && chown -R 999:999 /target && chmod 700 /target",
            ],
            timeout=1800,
        )
        findings.append(_finding("read_only_clone", copied.ok, "" if copied.ok else "clone failed"))
        if not copied.ok:
            return _report("clone_witness", findings)

        server = pool.name("container")
        script = (
            "printf '%s\\n' 'local all all trust' > /tmp/recovery-hba.conf\n"
            f'exec postgres -D "{_PGDATA}" -c listen_addresses= '
            f"-c unix_socket_directories={_SOCKET} -c hba_file=/tmp/recovery-hba.conf\n"
        )
        started = runner.run(
            [
                "run", "--detach", "--name", server, "--network", "none",
                "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID",
                "--cap-add", "SETGID", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
                "--user", "999:999", "--tmpfs", "/tmp", "--tmpfs", "/run",
                "--volume", f"{clone}:{_PGDATA}",
                "--entrypoint", "sh", postgres_image, "-ec", script,
            ],
            timeout=300,
        )
        findings.append(
            _finding("disposable_server", started.ok, "" if started.ok else "clone server failed")
        )
        if not started.ok:
            return _report("clone_witness", findings)

        answers: list[str] = []
        for query in witness_sql:
            result = runner.run(
                [
                    "exec", "--env", "PGOPTIONS=-c default_transaction_read_only=on", server,
                    "psql", "-h", _SOCKET, "-U", "postgres", "-d", "postgres",
                    "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query,
                ],
                timeout=300,
            )
            if not result.ok:
                findings.append(_finding("sql_witness", False, "read-only query failed"))
                return _report("clone_witness", findings)
            answers.append(result.stdout.strip())
        findings.append(_finding("sql_witness", True))
        digest = hashlib.sha256(
            json.dumps(answers, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return _report("clone_witness", findings, witness_digest=digest)
    finally:
        pool.release()


class SourceGateway(Protocol):
    """Доступ к редактируемому дереву проекта; в тестах подменяется двойником."""

    def read(self, path: str) -> bytes | None: ...

    def write(self, path: str, data: bytes) -> bool: ...

    def inventory(self) -> Mapping[str, str]: ...


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_sync(
    intent: RestorationRecoveryIntent,
    *,
    source: SourceGateway,
    page_path: str,
    expected_stale_digest: str,
    trusted_page_bytes: bytes,
    trusted_inventory: Mapping[str, str],
) -> RecoveryReport:
    """Вернуть ровно один известный устаревший файл к доверенным байтам снимка.

    Сначала сверка-и-замена: файл должен быть ИМЕННО тем устаревшим, который мы
    наблюдали. Если он другой — значит, с деревом что-то произошло после
    расследования, и восстановление останавливается, а не переписывает вслепую.
    Полная опись сверяется до и после: расхождение в любом другом файле — тоже
    остановка. База на этой фазе не монтируется вовсе.
    """
    findings: list[RecoveryFinding] = []

    observed = source.read(page_path)
    if observed is None:
        return _report("source_sync", [_finding("stale_page_present", False, "page not found")])
    observed_digest = _sha256(observed)
    if observed_digest != expected_stale_digest:
        # Может быть уже и целевое содержимое — тогда чинить нечего.
        already = observed_digest == _sha256(trusted_page_bytes)
        return _report(
            "source_sync",
            [
                _finding(
                    "source_changed",
                    False,
                    "the page already matches the trusted snapshot"
                    if already
                    else "the editable page is not the observed stale one",
                )
            ],
        )
    findings.append(_finding("stale_page_compare_and_swap", True))

    before = source.inventory()
    unexpected = sorted(
        path
        for path, digest in before.items()
        if path != page_path and trusted_inventory.get(path) != digest
    )
    missing = sorted(set(trusted_inventory) - set(before))
    if unexpected or missing:
        return _report(
            "source_sync",
            [
                _finding(
                    "inventory_matches_snapshot",
                    False,
                    f"{len(unexpected)} changed, {len(missing)} missing besides the known page",
                )
            ],
        )
    findings.append(_finding("inventory_matches_snapshot", True))

    if not source.write(page_path, trusted_page_bytes):
        return _report("source_sync", [*findings, _finding("page_restored", False, "write failed")])
    findings.append(_finding("page_restored", True))

    after = source.inventory()
    drift = sorted(path for path, digest in after.items() if trusted_inventory.get(path) != digest)
    findings.append(
        _finding(
            "inventory_equals_snapshot_after_write",
            not drift,
            f"{len(drift)} files differ from the snapshot" if drift else "",
        )
    )
    return _report("source_sync", findings)


def start_current(
    intent: RestorationRecoveryIntent,
    *,
    runner: DockerRunner,
    witness_digest: str,
    source_sync_ok: bool,
    witness_migrations: Sequence[str],
    snapshot_migrations: Sequence[str],
    start: Callable[[str], CommandResult],
) -> RecoveryReport:
    """Запустить текущую версию на той же удержанной базе — и ни на чём другом.

    Запуск разрешён только после доказанных предыдущих фаз: без SQL-свидетеля и без
    успешной сверки исходника стартовать нечего. База переиспользуется ровно та,
    что названа в намерении: ни создания нового тома, ни переинициализации.

    Отдельно проверяется цепочка миграций. Если снимок хочет применить миграции,
    которых нет в журнале живой базы, это блокирующая находка, а не разрешение
    «подогнать» схему: именно так теряют колонку с данными владельца.
    """
    findings: list[RecoveryFinding] = []

    findings.append(
        _finding(
            "witness_recorded",
            bool(re.fullmatch(r"[0-9a-f]{64}", witness_digest or "")),
            "" if witness_digest else "no SQL witness from the clone",
        )
    )
    findings.append(
        _finding(
            "source_sync_recorded",
            source_sync_ok,
            "" if source_sync_ok else "source not reconciled",
        )
    )
    # Снимок не должен доигрывать историю в уже изменённую схему.
    replay = [name for name in snapshot_migrations if name not in set(witness_migrations)]
    findings.append(
        _finding(
            "migration_chain_compatible",
            not replay,
            f"{len(replay)} migrations would be replayed into the live schema" if replay else "",
        )
    )
    if not all(item.ok for item in findings):
        return _report("start_current", findings)

    volumes = runner.run(["volume", "ls", "--quiet"], timeout=60)
    known = set(volumes.stdout.split())
    findings.append(
        _finding(
            "retained_database_present",
            intent.active_database_volume in known or not volumes.ok,
            "the bound database volume is gone"
            if volumes.ok and intent.active_database_volume not in known
            else "",
        )
    )
    if not all(item.ok for item in findings):
        return _report("start_current", findings)

    started = start(intent.active_database_volume)
    findings.append(
        _finding("current_started", started.ok, "" if started.ok else "start refused")
    )
    return _report("start_current", findings)


@dataclass(frozen=True, slots=True)
class OwnerBoundaryObservation:
    """Что увидели три участника: владелец строки, второй владелец и аноним."""

    owner_status: int
    owner_rows: int
    stranger_status: int
    stranger_rows: int
    anonymous_status: int
    baseline_digest_before: str
    baseline_digest_after: str
    temporary_row_lifecycle: Sequence[int] = ()


def verify_owner_boundary(observation: OwnerBoundaryObservation) -> RecoveryReport:
    """Пятая фаза: доказать, что данные видит только их владелец.

    Проверяется не «страница открылась», а граница: владелец читает свою строку,
    второй владелец не получает её ни в каком виде, аноним получает отказ. Отдельно
    временная строка проходит полный цикл создания, изменения, перечитывания и
    удаления — и при этом исходная строка обязана остаться нетронутой, что
    подтверждается одинаковым отпечатком до и после.
    """
    findings = [
        _finding(
            "owner_reads_own_row",
            observation.owner_status == 200 and observation.owner_rows == 1,
            f"owner saw {observation.owner_rows} rows (HTTP {observation.owner_status})"
            if observation.owner_status != 200 or observation.owner_rows != 1
            else "",
        ),
        _finding(
            "stranger_is_denied",
            observation.stranger_status in {403, 404} and observation.stranger_rows == 0,
            "a second signed identity reached the row"
            if observation.stranger_rows
            else f"unexpected status {observation.stranger_status}",
        ),
        _finding(
            "anonymous_is_denied",
            observation.anonymous_status == 401,
            f"anonymous got HTTP {observation.anonymous_status}",
        ),
        _finding(
            "baseline_row_untouched",
            observation.baseline_digest_before == observation.baseline_digest_after,
            "the baseline row changed during the check"
            if observation.baseline_digest_before != observation.baseline_digest_after
            else "",
        ),
        _finding(
            "temporary_row_full_lifecycle",
            list(observation.temporary_row_lifecycle) == [201, 200, 200, 204],
            "create/update/reload/delete did not all succeed",
        ),
    ]
    return _report("verify", findings)


__all__ = [
    "SCRATCH_PREFIX",
    "CommandResult",
    "DockerRunner",
    "OwnerBoundaryObservation",
    "ScratchPool",
    "SourceGateway",
    "clone_witness",
    "inspect_recovery",
    "source_sync",
    "start_current",
    "verify_owner_boundary",
]
