"""Восстановление после оборвавшегося отката не имеет права ошибиться томом.

21.09 проект остался расщеплённым: авторитетный снимок #7, редактируемое дерево от
#6, база отвязана и цела. Рядом лежит пустая устаревшая база — выбрать её «по
имени» значит потерять данные владельца, показав зелёный результат.

Эти тесты закрепляют два свойства до всякой реализации фаз: намерение нельзя
собрать из кусков разных проектов, и инспекция не смеет ничего запускать или
писать в оригинальный том.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yleum_orchestrator.schemas.restoration_recovery import (
    MUTATING_PHASES,
    RecoveryFinding,
    RecoveryReport,
    RestorationRecoveryIntent,
)

_HEX32 = "891ed2449b00458babf49f23f194aaab"
_OTHER32 = "aaaaaaaabbbbccccddddeeeeffff0000"
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _intent(**overrides: object) -> RestorationRecoveryIntent:
    payload: dict[str, object] = {
        "project_id": "361b3326-97b4-4c73-94d5-c8d277a69dc6",
        "owner_id": "f5a028d8-0000-4000-8000-000000000001",
        "workspace_id": "891ed244-9b00-458b-abf4-9f23f194aaab",
        "current_snapshot_id": "4d1089a5-ef13-5dcd-b313-7f0cf4b88c04",
        "current_commit_sha": "6371b71b9b3ca83ba31119bbc5b4bfc996cc4c94",
        "expected_fencing_epoch": 17,
        "registry_binding_digest": _DIGEST_A,
        "active_code_volume": f"omnia-machine-{_HEX32}-code-4ecaa09ac717487dbe39553062a537e5",
        "active_database_volume": f"omnia-machine-{_HEX32}-db-4ecaa09ac717487dbe39553062a537e5",
        "expected_workspace_inventory_digest": _DIGEST_A,
        "target_inventory_digest": _DIGEST_B,
        "original_db_content_manifest_digest": "c" * 64,
    }
    payload.update(overrides)
    return RestorationRecoveryIntent(**payload)  # type: ignore[arg-type]


def test_a_complete_intent_is_accepted_and_frozen() -> None:
    intent = _intent()

    assert intent.expected_fencing_epoch == 17
    with pytest.raises(ValidationError):
        intent.expected_fencing_epoch = 18  # type: ignore[misc]


def test_recovery_binds_registry_active_database_not_legacy() -> None:
    # Рядом с активным томом лежит пустая устаревшая база приложения. Она проходит
    # по форме имени, но принадлежит другой рабочей области — значит, не она.
    with pytest.raises(ValidationError):
        _intent(active_database_volume=f"omnia-machine-{_OTHER32}-app-postgres-data")


def test_an_intent_cannot_name_the_same_volume_twice() -> None:
    volume = f"omnia-machine-{_HEX32}-db-4ecaa09ac717487dbe39553062a537e5"
    with pytest.raises(ValidationError):
        _intent(active_code_volume=volume, active_database_volume=volume)


def test_an_intent_that_reconciles_nothing_is_refused() -> None:
    # Совпадение описей означает, что чинить нечего; такое намерение — ошибка сборки.
    with pytest.raises(ValidationError):
        _intent(target_inventory_digest=_DIGEST_A)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_commit_sha", "not-a-sha"),
        ("expected_fencing_epoch", 0),
        ("registry_binding_digest", "short"),
        ("active_code_volume", "omnia-machine-../etc/passwd"),
        ("active_database_volume", "postgres"),
    ],
)
def test_a_malformed_identity_is_refused_not_normalised(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _intent(**{field: value})


def test_no_extra_field_can_smuggle_a_docker_option() -> None:
    with pytest.raises(ValidationError):
        _intent(volumes=["/:/host"])


def test_a_failed_report_must_name_the_finding_that_failed() -> None:
    with pytest.raises(ValidationError):
        RecoveryReport(phase="inspect", ok=False, findings=())

    report = RecoveryReport(
        phase="inspect",
        ok=False,
        findings=(RecoveryFinding(check="fencing_epoch", ok=False, detail="17 != 18"),),
    )
    assert report.ok is False


def test_inspect_is_not_a_mutating_phase() -> None:
    # Инспекцию можно выполнять когда угодно именно потому, что она ничего не меняет.
    assert "inspect" not in MUTATING_PHASES
    assert MUTATING_PHASES[0] == "clone_witness"
    assert MUTATING_PHASES[-1] == "complete"


# --------------------------------------------------------------------------- #
# Инспекция и свидетель: оригинал не запускается и не изменяется
# --------------------------------------------------------------------------- #


class _Recorder:
    """Двойник docker: записывает каждый вызов и отвечает заранее заданным."""

    def __init__(self, answers: dict[str, object] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def run(self, args, *, timeout: float = 600.0):  # type: ignore[no-untyped-def]
        from yleum_orchestrator.services.restoration_recovery import CommandResult

        self.calls.append(list(args))
        key = " ".join(args[:2])
        answer = self.answers.get(key)
        if isinstance(answer, CommandResult):
            return answer
        if key == "volume inspect":
            return CommandResult(0, '{"Name": "x"}')
        return CommandResult(0, "")


def _registry(intent) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "workspace_id": str(intent.workspace_id),
        "active_code_volume": intent.active_code_volume,
        "active_database_volume": intent.active_database_volume,
    }


def test_inspection_never_starts_postgres_or_writes_original_volume() -> None:
    from yleum_orchestrator.services.restoration_recovery import inspect_recovery

    intent = _intent()
    recorder = _Recorder()

    report = inspect_recovery(intent, runner=recorder, registry=_registry(intent))

    assert report.ok is True and report.phase == "inspect"
    forbidden = ("run", "start", "exec", "rm", "cp", "commit", "volume create", "volume rm")
    for call in recorder.calls:
        head = " ".join(call[:2])
        assert call[0] not in ("run", "start", "exec", "rm", "cp", "commit"), call
        assert head not in ("volume create", "volume rm"), call
        # Оригинальный том упоминается только в фильтре чтения, никогда как монтирование.
        assert not any(
            part.startswith(f"{intent.active_database_volume}:") for part in call
        ), call
    assert forbidden  # список зафиксирован намеренно


def test_inspection_refuses_a_registry_that_names_another_volume() -> None:
    from yleum_orchestrator.services.restoration_recovery import inspect_recovery

    intent = _intent()
    registry = _registry(intent) | {
        "active_database_volume": f"omnia-machine-{_HEX32}-app-postgres-data"
    }

    report = inspect_recovery(intent, runner=_Recorder(), registry=registry)

    assert report.ok is False
    assert any(f.check == "registry_database_binding" and not f.ok for f in report.findings)


def test_inspection_stops_when_something_still_holds_the_database() -> None:
    from yleum_orchestrator.services.restoration_recovery import CommandResult, inspect_recovery

    intent = _intent()
    recorder = _Recorder({"ps --filter": CommandResult(0, "abc123\n")})

    report = inspect_recovery(intent, runner=recorder, registry=_registry(intent))

    assert report.ok is False
    assert any(f.check == "database_volume_detached" and not f.ok for f in report.findings)


def test_the_witness_mounts_the_original_read_only_and_starts_only_the_clone() -> None:
    from yleum_orchestrator.services.restoration_recovery import (
        SCRATCH_PREFIX,
        CommandResult,
        clone_witness,
    )

    intent = _intent()
    recorder = _Recorder({"exec --env": CommandResult(0, "1")})

    report = clone_witness(
        intent,
        runner=recorder,
        postgres_image="postgres@sha256:" + "a" * 64,
        helper_image="helper@sha256:" + "b" * 64,
        witness_sql=["select count(*) from qa_clients"],
    )

    assert report.ok is True and report.witness_digest is not None
    mounts = [part for call in recorder.calls for part in call if ":" in part and "/" in part]
    original = [m for m in mounts if m.startswith(intent.active_database_volume)]
    assert original and all(m.endswith(":ro") for m in original), original
    # Запускается только копия, и только под именем этого прогона.
    started = [call for call in recorder.calls if call[:2] == ["run", "--detach"]]
    assert len(started) == 1
    assert started[0][started[0].index("--name") + 1].startswith(SCRATCH_PREFIX)
    assert not any(
        part.startswith(f"{intent.active_database_volume}:{'/var'}") for part in started[0]
    )


def test_the_witness_removes_only_its_own_resources_even_on_failure() -> None:
    from yleum_orchestrator.services.restoration_recovery import (
        SCRATCH_PREFIX,
        CommandResult,
        clone_witness,
    )

    intent = _intent()
    recorder = _Recorder({"run --rm": CommandResult(1, "", "copy failed")})

    report = clone_witness(
        intent,
        runner=recorder,
        postgres_image="postgres@sha256:" + "a" * 64,
        helper_image="helper@sha256:" + "b" * 64,
        witness_sql=["select 1"],
    )

    assert report.ok is False
    removals = [
        call for call in recorder.calls if call[0] == "rm" or call[:2] == ["volume", "rm"]
    ]
    assert removals, "scratch resources must be released"
    for call in removals:
        assert any(part.startswith(SCRATCH_PREFIX) for part in call), call
        assert intent.active_database_volume not in call
        assert intent.active_code_volume not in call


def test_a_read_only_sql_failure_is_reported_not_swallowed() -> None:
    from yleum_orchestrator.services.restoration_recovery import CommandResult, clone_witness

    intent = _intent()
    recorder = _Recorder({"exec --env": CommandResult(1, "", "relation does not exist")})

    report = clone_witness(
        intent,
        runner=recorder,
        postgres_image="postgres@sha256:" + "a" * 64,
        helper_image="helper@sha256:" + "b" * 64,
        witness_sql=["select count(*) from qa_clients"],
    )

    assert report.ok is False and report.witness_digest is None
    assert any(f.check == "sql_witness" and not f.ok for f in report.findings)
    # Текст ошибки базы наружу не выносится.
    assert all("relation does not exist" not in f.detail for f in report.findings)


def test_refusing_to_touch_a_resource_outside_this_recovery() -> None:
    from yleum_orchestrator.services.restoration_recovery import ScratchPool

    pool = ScratchPool(_Recorder())
    pool.volumes.append("omnia-machine-891ed2449b00458babf49f23f194aaab-db-x")

    with pytest.raises(ValueError, match="outside this recovery"):
        pool.release()


# --------------------------------------------------------------------------- #
# RC3: вернуть ровно один известный устаревший файл, ничего больше
# --------------------------------------------------------------------------- #


class _Source:
    """Двойник редактируемого дерева проекта."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.writes: list[str] = []

    def read(self, path: str) -> bytes | None:
        return self.files.get(path)

    def write(self, path: str, data: bytes) -> bool:
        self.writes.append(path)
        self.files[path] = data
        return True

    def inventory(self) -> dict[str, str]:
        import hashlib

        return {p: hashlib.sha256(d).hexdigest() for p, d in sorted(self.files.items())}


_PAGE = "src/app/page.tsx"
_STALE = b"export default function Page(){return <h1>Controlnaya versiya v6</h1>}\n"
_TRUSTED = b"export default function Page(){return <h1>Controlnaya versiya v4</h1>}\n"


def _digest(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _tree(page: bytes = _STALE) -> _Source:
    return _Source({_PAGE: page, "package.json": b'{"name":"qa"}', "src/lib/db.ts": b"export {}\n"})


def _trusted_inventory(source: _Source) -> dict[str, str]:
    inventory = source.inventory()
    inventory[_PAGE] = _digest(_TRUSTED)
    return inventory


def test_recovery_replaces_only_known_stale_page() -> None:
    from yleum_orchestrator.services.restoration_recovery import source_sync

    source = _tree()
    report = source_sync(
        _intent(),
        source=source,
        page_path=_PAGE,
        expected_stale_digest=_digest(_STALE),
        trusted_page_bytes=_TRUSTED,
        trusted_inventory=_trusted_inventory(source),
    )

    assert report.ok is True
    assert source.writes == [_PAGE], "only the one known page may be written"
    assert source.files[_PAGE] == _TRUSTED
    assert source.files["src/lib/db.ts"] == b"export {}\n"


def test_a_page_that_is_not_the_observed_one_stops_the_recovery() -> None:
    from yleum_orchestrator.services.restoration_recovery import source_sync

    source = _tree(page=b"someone edited this meanwhile\n")
    report = source_sync(
        _intent(),
        source=source,
        page_path=_PAGE,
        expected_stale_digest=_digest(_STALE),
        trusted_page_bytes=_TRUSTED,
        trusted_inventory=_trusted_inventory(source),
    )

    assert report.ok is False
    assert any(f.check == "source_changed" for f in report.findings)
    assert source.writes == [], "nothing may be written after a failed compare-and-swap"


def test_an_already_restored_page_is_reported_not_rewritten() -> None:
    from yleum_orchestrator.services.restoration_recovery import source_sync

    source = _tree(page=_TRUSTED)
    report = source_sync(
        _intent(),
        source=source,
        page_path=_PAGE,
        expected_stale_digest=_digest(_STALE),
        trusted_page_bytes=_TRUSTED,
        trusted_inventory=_trusted_inventory(source),
    )

    assert report.ok is False
    assert any("already matches" in f.detail for f in report.findings)
    assert source.writes == []


def test_drift_in_another_file_cancels_instead_of_blanket_restore() -> None:
    from yleum_orchestrator.services.restoration_recovery import source_sync

    source = _tree()
    trusted = _trusted_inventory(source)
    source.files["src/lib/db.ts"] = b"export const changed = true\n"

    report = source_sync(
        _intent(),
        source=source,
        page_path=_PAGE,
        expected_stale_digest=_digest(_STALE),
        trusted_page_bytes=_TRUSTED,
        trusted_inventory=trusted,
    )

    assert report.ok is False
    assert any(f.check == "inventory_matches_snapshot" and not f.ok for f in report.findings)
    assert source.writes == []


def test_a_missing_file_is_not_silently_accepted() -> None:
    from yleum_orchestrator.services.restoration_recovery import source_sync

    source = _tree()
    trusted = _trusted_inventory(source)
    trusted["src/app/layout.tsx"] = _digest(b"missing from the tree")

    report = source_sync(
        _intent(),
        source=source,
        page_path=_PAGE,
        expected_stale_digest=_digest(_STALE),
        trusted_page_bytes=_TRUSTED,
        trusted_inventory=trusted,
    )

    assert report.ok is False and source.writes == []


def test_the_result_is_checked_again_after_the_write() -> None:
    from yleum_orchestrator.services.restoration_recovery import source_sync

    class _Liar(_Source):
        def write(self, path: str, data: bytes) -> bool:
            self.writes.append(path)
            self.files[path] = b"not what was asked for\n"
            return True

    source = _Liar(_tree().files)
    report = source_sync(
        _intent(),
        source=source,
        page_path=_PAGE,
        expected_stale_digest=_digest(_STALE),
        trusted_page_bytes=_TRUSTED,
        trusted_inventory=_trusted_inventory(source),
    )

    assert report.ok is False
    assert any(
        f.check == "inventory_equals_snapshot_after_write" and not f.ok for f in report.findings
    )


# --------------------------------------------------------------------------- #
# RC4: запуск только после доказанных фаз и только на удержанной базе
# --------------------------------------------------------------------------- #


def _start_recorder():  # type: ignore[no-untyped-def]
    from yleum_orchestrator.services.restoration_recovery import CommandResult

    calls: list[str] = []

    def start(volume: str) -> CommandResult:
        calls.append(volume)
        return CommandResult(0)

    return start, calls


def _volumes_runner(intent):  # type: ignore[no-untyped-def]
    from yleum_orchestrator.services.restoration_recovery import CommandResult

    return _Recorder(
        {
            "volume ls": CommandResult(
                0, f"{intent.active_database_volume}\n{intent.active_code_volume}\n"
            )
        }
    )


def test_start_current_reuses_the_bound_database_and_creates_nothing() -> None:
    from yleum_orchestrator.services.restoration_recovery import start_current

    intent = _intent()
    runner = _volumes_runner(intent)
    start, started = _start_recorder()

    report = start_current(
        intent,
        runner=runner,
        witness_digest="d" * 64,
        source_sync_ok=True,
        witness_migrations=["0000_init", "0001_qa_clients"],
        snapshot_migrations=["0000_init", "0001_qa_clients"],
        start=start,
    )

    assert report.ok is True
    assert started == [intent.active_database_volume]
    for call in runner.calls:
        assert call[:2] != ["volume", "create"], call
        assert call[:2] != ["volume", "rm"], call


def test_start_current_refuses_without_a_sql_witness() -> None:
    from yleum_orchestrator.services.restoration_recovery import start_current

    intent = _intent()
    start, started = _start_recorder()

    report = start_current(
        intent,
        runner=_volumes_runner(intent),
        witness_digest="",
        source_sync_ok=True,
        witness_migrations=["0000_init"],
        snapshot_migrations=["0000_init"],
        start=start,
    )

    assert report.ok is False and started == []
    assert any(f.check == "witness_recorded" and not f.ok for f in report.findings)


def test_start_current_refuses_before_the_source_is_reconciled() -> None:
    from yleum_orchestrator.services.restoration_recovery import start_current

    intent = _intent()
    start, started = _start_recorder()

    report = start_current(
        intent,
        runner=_volumes_runner(intent),
        witness_digest="d" * 64,
        source_sync_ok=False,
        witness_migrations=["0000_init"],
        snapshot_migrations=["0000_init"],
        start=start,
    )

    assert report.ok is False and started == []
    assert any(f.check == "source_sync_recorded" and not f.ok for f in report.findings)


def test_a_migration_the_live_schema_never_saw_is_a_blocking_finding() -> None:
    from yleum_orchestrator.services.restoration_recovery import start_current

    intent = _intent()
    start, started = _start_recorder()

    report = start_current(
        intent,
        runner=_volumes_runner(intent),
        witness_digest="d" * 64,
        source_sync_ok=True,
        # Живая база знает только QA-миграцию; снимок хочет доиграть служебные.
        witness_migrations=["0001_qa_clients"],
        snapshot_migrations=["0000_max_core", "0001_qa_clients"],
        start=start,
    )

    assert report.ok is False and started == [], "a replay must never be attempted"
    finding = next(f for f in report.findings if f.check == "migration_chain_compatible")
    assert finding.ok is False and "replayed" in finding.detail


def test_a_missing_retained_volume_stops_before_start() -> None:
    from yleum_orchestrator.services.restoration_recovery import CommandResult, start_current

    intent = _intent()
    runner = _Recorder({"volume ls": CommandResult(0, "some-other-volume\n")})
    start, started = _start_recorder()

    report = start_current(
        intent,
        runner=runner,
        witness_digest="d" * 64,
        source_sync_ok=True,
        witness_migrations=["0000_init"],
        snapshot_migrations=["0000_init"],
        start=start,
    )

    assert report.ok is False and started == []
    assert any(f.check == "retained_database_present" and not f.ok for f in report.findings)


# --------------------------------------------------------------------------- #
# RC5: данные видит только их владелец, исходная строка не меняется
# --------------------------------------------------------------------------- #


def _observation(**overrides):  # type: ignore[no-untyped-def]
    from yleum_orchestrator.services.restoration_recovery import OwnerBoundaryObservation

    payload = {
        "owner_status": 200,
        "owner_rows": 1,
        "stranger_status": 404,
        "stranger_rows": 0,
        "anonymous_status": 401,
        "baseline_digest_before": "f" * 64,
        "baseline_digest_after": "f" * 64,
        "temporary_row_lifecycle": (201, 200, 200, 204),
    }
    payload.update(overrides)
    return OwnerBoundaryObservation(**payload)  # type: ignore[arg-type]


def test_the_owner_boundary_passes_only_when_every_part_holds() -> None:
    from yleum_orchestrator.services.restoration_recovery import verify_owner_boundary

    report = verify_owner_boundary(_observation())

    assert report.ok is True and report.phase == "verify"


def test_a_second_signed_identity_that_sees_the_row_fails_the_phase() -> None:
    from yleum_orchestrator.services.restoration_recovery import verify_owner_boundary

    report = verify_owner_boundary(_observation(stranger_status=200, stranger_rows=1))

    assert report.ok is False
    assert any(f.check == "stranger_is_denied" and not f.ok for f in report.findings)


def test_an_anonymous_read_that_succeeds_fails_the_phase() -> None:
    from yleum_orchestrator.services.restoration_recovery import verify_owner_boundary

    report = verify_owner_boundary(_observation(anonymous_status=200))

    assert report.ok is False
    assert any(f.check == "anonymous_is_denied" and not f.ok for f in report.findings)


def test_a_changed_baseline_row_fails_even_if_everything_else_is_green() -> None:
    from yleum_orchestrator.services.restoration_recovery import verify_owner_boundary

    report = verify_owner_boundary(_observation(baseline_digest_after="e" * 64))

    assert report.ok is False
    assert any(f.check == "baseline_row_untouched" and not f.ok for f in report.findings)


def test_reading_the_row_is_not_enough_without_the_full_temporary_lifecycle() -> None:
    from yleum_orchestrator.services.restoration_recovery import verify_owner_boundary

    # Создали и изменили, но удаление не прошло — приложение не доказано рабочим.
    report = verify_owner_boundary(_observation(temporary_row_lifecycle=(201, 200, 200, 500)))

    assert report.ok is False
    assert any(f.check == "temporary_row_full_lifecycle" and not f.ok for f in report.findings)


def test_an_empty_owner_read_is_not_a_pass() -> None:
    from yleum_orchestrator.services.restoration_recovery import verify_owner_boundary

    report = verify_owner_boundary(_observation(owner_rows=0))

    assert report.ok is False
    assert any(f.check == "owner_reads_own_row" and not f.ok for f in report.findings)


# --------------------------------------------------------------------------- #
# RC6: восстановление завершено, только если ничего лишнего не появилось
# --------------------------------------------------------------------------- #


def _completion(**overrides):  # type: ignore[no-untyped-def]
    from yleum_orchestrator.services.restoration_recovery import CompletionObservation

    payload = {
        "current_version_number": 7,
        "current_snapshot_id": "4d1089a5-ef13-5dcd-b313-7f0cf4b88c04",
        "failed_version_numbers": (8,),
        "version_count_before": 8,
        "version_count_after": 8,
        "settlements_during_recovery": 0,
        "generation_runs_during_recovery": 0,
        "serving_commit_sha": "6371b71b9b3ca83ba31119bbc5b4bfc996cc4c94",
        "editable_inventory_digest": "d" * 64,
        "snapshot_inventory_digest": "d" * 64,
        "open_leases": 0,
    }
    payload.update(overrides)
    return CompletionObservation(**payload)  # type: ignore[arg-type]


def test_a_clean_recovery_completes() -> None:
    from yleum_orchestrator.services.restoration_recovery import complete_recovery

    report = complete_recovery(_intent(), _completion())

    assert report.ok is True and report.phase == "complete"


def test_recovery_that_created_a_version_is_not_a_recovery() -> None:
    from yleum_orchestrator.services.restoration_recovery import complete_recovery

    report = complete_recovery(_intent(), _completion(version_count_after=9))

    assert report.ok is False
    finding = next(f for f in report.findings if f.check == "no_new_version")
    assert not finding.ok and "8 -> 9" in finding.detail


def test_recovery_must_not_spend_quota_or_settle() -> None:
    from yleum_orchestrator.services.restoration_recovery import complete_recovery

    for field in ("settlements_during_recovery", "generation_runs_during_recovery"):
        report = complete_recovery(_intent(), _completion(**{field: 1}))
        assert report.ok is False, field
        assert any(f.check == "recovery_spent_nothing" and not f.ok for f in report.findings)


def test_the_failed_attempt_stays_failed() -> None:
    from yleum_orchestrator.services.restoration_recovery import complete_recovery

    # Упавшая версия стала текущей — историю переписали.
    report = complete_recovery(_intent(), _completion(current_version_number=8))

    assert report.ok is False
    assert any(f.check == "failed_version_stays_failed" and not f.ok for f in report.findings)


def test_serving_code_must_be_the_restored_commit() -> None:
    from yleum_orchestrator.services.restoration_recovery import complete_recovery

    report = complete_recovery(_intent(), _completion(serving_commit_sha="0" * 40))

    assert report.ok is False
    assert any(f.check == "serving_matches_the_snapshot" and not f.ok for f in report.findings)


def test_an_editable_tree_that_still_differs_blocks_completion() -> None:
    from yleum_orchestrator.services.restoration_recovery import complete_recovery

    report = complete_recovery(_intent(), _completion(editable_inventory_digest="e" * 64))

    assert report.ok is False
    assert any(f.check == "editable_tree_equals_snapshot" and not f.ok for f in report.findings)


def test_an_open_lease_blocks_completion() -> None:
    from yleum_orchestrator.services.restoration_recovery import complete_recovery

    report = complete_recovery(_intent(), _completion(open_leases=1))

    assert report.ok is False
    assert any(f.check == "no_lease_left_open" and not f.ok for f in report.findings)
