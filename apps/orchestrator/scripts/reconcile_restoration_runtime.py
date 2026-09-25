#!/usr/bin/env python3
"""Провести одну фазу восстановления оборвавшегося отката — и ровно одну.

Инструмент намеренно неудобен. У него нет флагов проекта, тома или владельца:
всё, что он трогает, названо заранее в приватном файле намерения, а каждая фаза
выполняется отдельным запуском после PASS предыдущей. Так нельзя случайно
«починить» соседний проект, подставив не тот идентификатор в командной строке.

    reconcile_restoration_runtime.py --intent-file "$RECOVERY_INTENT" --phase inspect
    reconcile_restoration_runtime.py --intent-file "$RECOVERY_INTENT" --phase clone_witness

``inspect`` ничего не запускает и не пишет: это чтение реестра и меток томов,
допустимое когда угодно. Остальные фазы меняют состояние и идут через журнал
(``--journal``, по умолчанию рядом с файлом намерения). Журнал помнит пройденные
фазы: повтор после обрыва не выполняет эффект второй раз, а чужое намерение или
сменившаяся ограда дают отказ 409.

Часть входных данных фазы не помещается в намерение — образы, SQL свидетеля,
доверенные байты снимка, наблюдения после запуска. Они лежат в том же приватном
файле в разделе ``inputs`` по имени фазы. Чего не хватает — то названо в отказе,
а не подставлено умолчанием.

Код выхода: 0 — фаза прошла, 1 — фаза не прошла (находки в выводе), 2 — отказ
(конфликт намерения, нарушенный порядок, нехватка входных данных).

Вывод — JSON с находками и отпечатками. Ни DSN, ни переменных окружения, ни
содержимого строк владельца в нём нет и быть не должно.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yleum_orchestrator.schemas.restoration_recovery import (
    MUTATING_PHASES,
    RestorationRecoveryIntent,
)
from yleum_orchestrator.services.restoration_recovery import (
    CommandResult,
    CompletionObservation,
    JournalEntry,
    OwnerBoundaryObservation,
    RecoveryConflict,
    advance_recovery,
    clone_witness,
    complete_recovery,
    inspect_recovery,
    intent_digest,
    source_sync,
    start_current,
    verify_owner_boundary,
)

_PHASES = ("inspect", *MUTATING_PHASES)
_EXIT_OK, _EXIT_FAILED, _EXIT_REFUSED = 0, 1, 2


class MissingInput(RecoveryConflict):
    """Фазе не хватает входных данных: отказ, а не догадка об умолчании.

    Отдельный код от конфликта намерения: там продолжают чужое восстановление,
    здесь — своё, но без того, чем его выполняют.
    """

    status_code = 422


class _Docker:
    """Единственный путь наружу; вызывается только с явными аргументами."""

    def run(self, args: Sequence[str], *, timeout: float = 600.0) -> CommandResult:
        try:
            done = subprocess.run(
                ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CommandResult(exit_code=125, stderr=type(exc).__name__)
        return CommandResult(done.returncode, done.stdout or "", done.stderr or "")


class _FileJournal:
    """Дозапись по одной строке JSON: переживает перезапуск процесса и машины."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def entries(self, project_id: str) -> list[JournalEntry]:
        if not self._path.exists():
            return []
        rows: list[JournalEntry] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                if payload.get("project_id") == project_id:
                    rows.append(JournalEntry(**payload))
        return rows

    def append(self, entry: JournalEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "project_id": entry.project_id,
                "intent_digest": entry.intent_digest,
                "fencing_epoch": entry.fencing_epoch,
                "phase": entry.phase,
                "ok": entry.ok,
                "witness_digest": entry.witness_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        # Сброс на диск сразу: журнал должен пережить обрыв ровно на этой строке,
        # иначе фаза повторится, а её эффект уже случился.
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class _TreeGateway:
    """Редактируемое дерево проекта как файлы под одним корнем."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _resolve(self, path: str) -> Path:
        target = (self._root / path).resolve()
        if not target.is_relative_to(self._root):
            raise MissingInput(f"path escapes the workspace root: {path!r}")
        return target

    def read(self, path: str) -> bytes | None:
        target = self._resolve(path)
        return target.read_bytes() if target.is_file() else None

    def write(self, path: str, data: bytes) -> bool:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return True

    def inventory(self) -> dict[str, str]:
        return {
            str(item.relative_to(self._root)): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(self._root.rglob("*"))
            if item.is_file()
        }


def _need(inputs: Mapping[str, object], phase: str, *names: str) -> tuple[object, ...]:
    block = inputs.get(phase)
    if not isinstance(block, Mapping):
        raise MissingInput(f"inputs.{phase} is missing from the intent file")
    missing = [name for name in names if block.get(name) is None]
    if missing:
        raise MissingInput(f"inputs.{phase} lacks {', '.join(missing)}")
    return tuple(block[name] for name in names)


def _journal_facts(journal: _FileJournal, project_id: str) -> tuple[str | None, bool]:
    """Свидетель и результат сверки исходника берутся из журнала, не с клавиатуры."""
    witness, synced = None, False
    for entry in journal.entries(project_id):
        if entry.phase == "clone_witness" and entry.ok and entry.witness_digest:
            witness = entry.witness_digest
        if entry.phase == "source_sync" and entry.ok:
            synced = True
    return witness, synced


def _build_phase(
    phase: str,
    intent: RestorationRecoveryIntent,
    inputs: Mapping[str, object],
    runner: _Docker,
    journal: _FileJournal,
):
    """Вернуть пару «выполнить фазу» и «эффект уже на месте?»."""
    project_id = str(intent.project_id)

    if phase == "clone_witness":
        postgres_image, helper_image, witness_sql = _need(
            inputs, phase, "postgres_image", "helper_image", "witness_sql"
        )
        return (
            lambda: clone_witness(
                intent,
                runner=runner,
                postgres_image=str(postgres_image),
                helper_image=str(helper_image),
                witness_sql=str(witness_sql),
            ),
            None,
        )

    if phase == "source_sync":
        workspace_root, page_path, stale_digest, trusted_root = _need(
            inputs, phase, "workspace_root", "page_path", "expected_stale_digest", "trusted_root"
        )
        gateway = _TreeGateway(Path(str(workspace_root)))
        trusted = _TreeGateway(Path(str(trusted_root)))
        trusted_bytes = trusted.read(str(page_path))
        if trusted_bytes is None:
            raise MissingInput(f"the trusted snapshot has no {page_path!r}")
        trusted_inventory = trusted.inventory()

        def already() -> bool:
            # Обрыв между записью и журналом: дерево уже целевое целиком.
            return gateway.inventory() == trusted_inventory

        return (
            lambda: source_sync(
                intent,
                source=gateway,
                page_path=str(page_path),
                expected_stale_digest=str(stale_digest),
                trusted_page_bytes=trusted_bytes,
                trusted_inventory=trusted_inventory,
            ),
            already,
        )

    if phase == "start_current":
        witness_migrations, snapshot_migrations, start_command, container = _need(
            inputs, phase, "witness_migrations", "snapshot_migrations", "start_command", "container"
        )
        witness, synced = _journal_facts(journal, project_id)
        if witness is None:
            raise MissingInput("no witness digest recorded by clone_witness")

        def already() -> bool:
            probe = runner.run(
                ["inspect", "--format", "{{.State.Running}}", str(container)], timeout=60
            )
            return probe.ok and probe.stdout.strip() == "true"

        return (
            lambda: start_current(
                intent,
                runner=runner,
                witness_digest=witness,
                source_sync_ok=synced,
                witness_migrations=list(witness_migrations),  # type: ignore[arg-type]
                snapshot_migrations=list(snapshot_migrations),  # type: ignore[arg-type]
                start=lambda volume: runner.run([*list(start_command), volume]),  # type: ignore[misc]
            ),
            already,
        )

    if phase == "verify":
        (observation,) = _need(inputs, phase, "observation")
        return (lambda: verify_owner_boundary(OwnerBoundaryObservation(**observation)), None)  # type: ignore[arg-type]

    (observation,) = _need(inputs, phase, "observation")
    return (lambda: complete_recovery(intent, CompletionObservation(**observation)), None)  # type: ignore[arg-type]


def _load(
    path: Path,
) -> tuple[RestorationRecoveryIntent, Mapping[str, object], Mapping[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    registry = payload.pop("registry", {})
    inputs = payload.pop("inputs", {})
    intent = RestorationRecoveryIntent(**payload)
    return intent, registry if isinstance(registry, Mapping) else {}, (
        inputs if isinstance(inputs, Mapping) else {}
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--intent-file", required=True, type=Path, help="приватный файл намерения")
    parser.add_argument("--phase", required=True, choices=_PHASES)
    parser.add_argument("--journal", type=Path, default=None, help="по умолчанию <intent>.journal")
    args = parser.parse_args(argv)

    try:
        intent, registry, inputs = _load(args.intent_file)
    except (OSError, ValueError) as exc:
        print(json.dumps({"refused": "intent_unreadable", "error": type(exc).__name__}))
        return _EXIT_REFUSED

    runner = _Docker()

    if args.phase == "inspect":
        report = inspect_recovery(intent, runner=runner, registry=registry)
    else:
        journal = _FileJournal(args.journal or args.intent_file.with_suffix(".journal"))
        # Фаза собирается лениво. Сначала журнал решает, законно ли её вообще
        # трогать: уже пройденной фазе и чужому намерению входные данные не нужны,
        # и требовать их раньше отказа значит отказывать не по той причине.
        built: list[tuple] = []

        def _phase() -> tuple:
            if not built:
                built.append(_build_phase(args.phase, intent, inputs, runner, journal))
            return built[0]

        try:
            report = advance_recovery(
                intent,
                args.phase,
                journal=journal,
                perform=lambda: _phase()[0](),
                already_done=lambda: bool(_phase()[1] and _phase()[1]()),
            )
        except RecoveryConflict as exc:
            print(
                json.dumps(
                    {"refused": type(exc).__name__, "status": exc.status_code, "detail": str(exc)},
                    ensure_ascii=False,
                )
            )
            return _EXIT_REFUSED

    print(
        json.dumps(
            {
                "phase": report.phase,
                "ok": report.ok,
                "intent_digest": intent_digest(intent),
                "witness_digest": report.witness_digest,
                "findings": [
                    {"check": f.check, "ok": f.ok, "detail": f.detail} for f in report.findings
                ],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return _EXIT_OK if report.ok else _EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
