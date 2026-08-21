#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

_TOKEN_PATTERN = re.compile(r"[1-9][0-9]{5,14}:[A-Za-z0-9_-]{20,}")
_GROUP_CHAT_PATTERN = re.compile(r"-[1-9][0-9]{5,}")


class TelegramReportError(RuntimeError):
    pass


def _missing_result_message() -> str:
    return "❌ Тестовые генерации не прошли.\nОшибка: Не удалось получить результат проверки."


def build_message(result_path: Path) -> str:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _missing_result_message()
    if not isinstance(result, dict):
        return _missing_result_message()
    if result.get("status") == "success":
        return "✅ Тестовые генерации успешно прошли."
    error = result.get("error")
    if (
        result.get("status") != "failure"
        or not isinstance(error, str)
        or not error
        or len(error) > 160
        or "\n" in error
        or "\r" in error
    ):
        return _missing_result_message()
    return f"❌ Тестовые генерации не прошли.\nОшибка: {error}"


def build_request(*, token: str, chat_id: str, message: str) -> Request:
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "disable_web_page_preview": True,
            "text": message,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def send_report(
    *,
    token: str,
    chat_id: str,
    message: str,
    opener: object = urlopen,
) -> None:
    request = build_request(token=token, chat_id=chat_id, message=message)
    try:
        with opener(request, timeout=15) as response:  # type: ignore[operator]
            status = response.status
            body = response.read(4096)
        payload = json.loads(body)
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        raise TelegramReportError("telegram delivery failed") from exc
    if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
        raise TelegramReportError("telegram delivery failed")


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    result_file = os.getenv("PRODUCTION_CANARY_RESULT_FILE")
    if (
        not token
        or _TOKEN_PATTERN.fullmatch(token) is None
        or not chat_id
        or _GROUP_CHAT_PATTERN.fullmatch(chat_id) is None
        or not result_file
    ):
        print("telegram generation report configuration invalid", file=sys.stderr)
        return 1
    try:
        send_report(
            token=token,
            chat_id=chat_id,
            message=build_message(Path(result_file)),
        )
    except TelegramReportError:
        print("telegram generation report delivery failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
