"""Инструмент восстановления обязан отказывать раньше, чем что-то трогает.

У него нет флагов проекта и тома: цель названа в приватном файле намерения. Эти
тесты закрепляют границы, ради которых он такой неудобный — нельзя перескочить
фазу, нельзя подставить умолчание вместо недостающего входа, нельзя вывести
наружу строку подключения, и нельзя выполнить эффект второй раз после обрыва.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from .test_restoration_recovery import _intent

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reconcile_restoration_runtime.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("reconcile_restoration_runtime", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _intent_file(tmp_path: Path, **payload: object) -> Path:
    body = json.loads(_intent().model_dump_json())
    body.update(payload)
    path = tmp_path / "recovery-intent.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_phase_cannot_be_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _intent_file(tmp_path, inputs={"start_current": {"container": "x"}})

    code = cli.main(["--intent-file", str(path), "--phase", "start_current"])
    printed = json.loads(capsys.readouterr().out)

    assert code == 2
    assert printed["refused"] == "RecoveryConflict" and printed["status"] == 409


def test_a_missing_input_is_named_not_guessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _intent_file(tmp_path, inputs={"clone_witness": {"postgres_image": "postgres:16"}})

    code = cli.main(["--intent-file", str(path), "--phase", "clone_witness"])
    printed = json.loads(capsys.readouterr().out)

    assert code == 2 and printed["status"] == 422
    assert "helper_image" in printed["detail"] and "witness_sql" in printed["detail"]


def test_an_unreadable_intent_refuses_without_touching_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    code = cli.main(["--intent-file", str(path), "--phase", "inspect"])
    printed = json.loads(capsys.readouterr().out)

    assert code == 2 and printed["refused"] == "intent_unreadable"
    # Причина названа классом ошибки, а не содержимым приватного файла.
    assert "not json" not in json.dumps(printed)


def test_a_restored_tree_is_resumed_and_not_rewritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Обрыв между записью и журналом: дерево уже целевое, повторять нечего."""
    workspace, trusted = tmp_path / "ws", tmp_path / "snap"
    for root in (workspace, trusted):
        (root / "src" / "app").mkdir(parents=True)
        (root / "src" / "app" / "page.tsx").write_bytes(b"restored")
        (root / "README.md").write_bytes(b"same")

    journal = tmp_path / "j.journal"
    journal.write_text(
        json.dumps(
            {
                "project_id": str(_intent().project_id),
                "intent_digest": cli.intent_digest(_intent()),
                "fencing_epoch": _intent().expected_fencing_epoch,
                "phase": "clone_witness",
                "ok": True,
                "witness_digest": "d" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    path = _intent_file(
        tmp_path,
        inputs={
            "source_sync": {
                "workspace_root": str(workspace),
                "page_path": "src/app/page.tsx",
                # Устаревшего содержимого уже нет — оно было заменено до обрыва.
                "expected_stale_digest": hashlib.sha256(b"stale").hexdigest(),
                "trusted_root": str(trusted),
            }
        },
    )

    code = cli.main(
        ["--intent-file", str(path), "--phase", "source_sync", "--journal", str(journal)]
    )
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert any(item["check"] == "resumed" for item in printed["findings"])
    assert (workspace / "src" / "app" / "page.tsx").read_bytes() == b"restored"


def test_a_passed_phase_is_replayed_from_the_journal_on_a_new_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "j.journal"
    journal.write_text(
        json.dumps(
            {
                "project_id": str(_intent().project_id),
                "intent_digest": cli.intent_digest(_intent()),
                "fencing_epoch": _intent().expected_fencing_epoch,
                "phase": "clone_witness",
                "ok": True,
                "witness_digest": "d" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # Входных данных нет вовсе: пройденная фаза не имеет права их потребовать.
    path = _intent_file(tmp_path)

    code = cli.main(
        ["--intent-file", str(path), "--phase", "clone_witness", "--journal", str(journal)]
    )
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert any(item["check"] == "replayed" for item in printed["findings"])


def test_a_journal_from_another_intent_is_a_conflict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "j.journal"
    journal.write_text(
        json.dumps(
            {
                "project_id": str(_intent().project_id),
                "intent_digest": "0" * 64,
                "fencing_epoch": 17,
                "phase": "clone_witness",
                "ok": True,
                "witness_digest": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    path = _intent_file(tmp_path, inputs={"source_sync": {}})

    code = cli.main(
        ["--intent-file", str(path), "--phase", "source_sync", "--journal", str(journal)]
    )
    printed = json.loads(capsys.readouterr().out)

    assert code == 2 and printed["status"] == 409


def test_the_workspace_gateway_refuses_to_leave_its_root(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (tmp_path / "secret.txt").write_bytes(b"not yours")
    gateway = cli._TreeGateway(root)

    with pytest.raises(cli.MissingInput):
        gateway.read("../secret.txt")


def test_the_output_never_carries_a_connection_string(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _intent_file(
        tmp_path,
        registry={"dsn": "postgresql://omnia:hunter2@127.0.0.1:5432/app"},
        inputs={},
    )

    cli.main(["--intent-file", str(path), "--phase", "inspect"])
    printed = capsys.readouterr().out

    assert "hunter2" not in printed and "postgresql://" not in printed


def test_only_a_mutating_phase_can_be_asked_for(tmp_path: Path) -> None:
    path = _intent_file(tmp_path)

    with pytest.raises(SystemExit):
        cli.main(["--intent-file", str(path), "--phase", "definitely-not-a-phase"])
