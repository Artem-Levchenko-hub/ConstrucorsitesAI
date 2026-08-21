from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.request import Request

import pytest

REPORTER_PATH = (
    Path(__file__).resolve().parents[3] / "infra" / "monitoring" / "telegram_generation_report.py"
)


def _load_reporter() -> ModuleType:
    if not REPORTER_PATH.is_file():
        pytest.fail("Telegram generation reporter is missing")
    spec = importlib.util.spec_from_file_location("telegram_generation_report", REPORTER_PATH)
    if spec is None or spec.loader is None:
        pytest.fail("Telegram generation reporter cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_success_report_contains_only_generation_outcome(tmp_path: Path) -> None:
    reporter = _load_reporter()
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"status": "success"}), encoding="utf-8")

    assert reporter.build_message(result_path) == "✅ Тестовые генерации успешно прошли."


def test_failure_report_contains_safe_error(tmp_path: Path) -> None:
    reporter = _load_reporter()
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "failure",
                "error": "Превью тестовой генерации не запустилось.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert reporter.build_message(result_path) == (
        "❌ Тестовые генерации не прошли.\nОшибка: Превью тестовой генерации не запустилось."
    )


def test_missing_result_reports_that_generations_did_not_start(tmp_path: Path) -> None:
    reporter = _load_reporter()

    assert reporter.build_message(tmp_path / "missing.json") == (
        "❌ Тестовые генерации не прошли.\nОшибка: Не удалось получить результат проверки."
    )


def test_malformed_result_uses_fixed_error_without_file_contents(tmp_path: Path) -> None:
    reporter = _load_reporter()
    result_path = tmp_path / "result.json"
    result_path.write_text("private response body", encoding="utf-8")

    message = reporter.build_message(result_path)

    assert message == (
        "❌ Тестовые генерации не прошли.\nОшибка: Не удалось получить результат проверки."
    )
    assert "private response body" not in message


def test_request_targets_telegram_and_sends_only_chat_and_message() -> None:
    reporter = _load_reporter()
    assert hasattr(reporter, "build_request"), "Telegram request builder is missing"

    request = reporter.build_request(
        token="123456789:abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJ",
        chat_id="-1001234567890",
        message="✅ Тестовые генерации успешно прошли.",
    )

    assert isinstance(request, Request)
    assert request.full_url == (
        "https://api.telegram.org/bot123456789:abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJ/sendMessage"
    )
    assert json.loads(request.data.decode("utf-8")) == {
        "chat_id": "-1001234567890",
        "disable_web_page_preview": True,
        "text": "✅ Тестовые генерации успешно прошли.",
    }


def test_main_rejects_missing_telegram_credentials_without_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    reporter = _load_reporter()
    assert hasattr(reporter, "main"), "Telegram reporter entry point is missing"

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"status": "success"}), encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(result_path))

    assert reporter.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "telegram generation report configuration invalid\n"


def test_sender_rejects_unsuccessful_telegram_response() -> None:
    reporter = _load_reporter()
    assert hasattr(reporter, "send_report"), "Telegram sender is missing"

    class TelegramResponse:
        status = 200

        def __enter__(self) -> TelegramResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b'{"ok":false}'

    def open_url(_request: Request, *, timeout: float) -> Any:
        return TelegramResponse()

    with pytest.raises(reporter.TelegramReportError):
        reporter.send_report(
            token="123456789:abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJ",
            chat_id="-1001234567890",
            message="✅ Тестовые генерации успешно прошли.",
            opener=open_url,
        )
