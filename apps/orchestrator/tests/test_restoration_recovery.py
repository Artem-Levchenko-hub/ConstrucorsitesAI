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

from omnia_orchestrator.schemas.restoration_recovery import (
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
        from omnia_orchestrator.services.restoration_recovery import CommandResult

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
    from omnia_orchestrator.services.restoration_recovery import inspect_recovery

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
    from omnia_orchestrator.services.restoration_recovery import inspect_recovery

    intent = _intent()
    registry = _registry(intent) | {
        "active_database_volume": f"omnia-machine-{_HEX32}-app-postgres-data"
    }

    report = inspect_recovery(intent, runner=_Recorder(), registry=registry)

    assert report.ok is False
    assert any(f.check == "registry_database_binding" and not f.ok for f in report.findings)


def test_inspection_stops_when_something_still_holds_the_database() -> None:
    from omnia_orchestrator.services.restoration_recovery import CommandResult, inspect_recovery

    intent = _intent()
    recorder = _Recorder({"ps --filter": CommandResult(0, "abc123\n")})

    report = inspect_recovery(intent, runner=recorder, registry=_registry(intent))

    assert report.ok is False
    assert any(f.check == "database_volume_detached" and not f.ok for f in report.findings)


def test_the_witness_mounts_the_original_read_only_and_starts_only_the_clone() -> None:
    from omnia_orchestrator.services.restoration_recovery import (
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
    from omnia_orchestrator.services.restoration_recovery import (
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
    from omnia_orchestrator.services.restoration_recovery import CommandResult, clone_witness

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
    from omnia_orchestrator.services.restoration_recovery import ScratchPool

    pool = ScratchPool(_Recorder())
    pool.volumes.append("omnia-machine-891ed2449b00458babf49f23f194aaab-db-x")

    with pytest.raises(ValueError, match="outside this recovery"):
        pool.release()
