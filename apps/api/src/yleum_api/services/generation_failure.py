"""Allowlisted failure summaries for durable message history; never echo logs."""

import re

from yleum_api.models.generation_run import GenerationRun
from yleum_api.schemas.message import GenerationFailure


def public_generation_failure(run: GenerationRun | None) -> GenerationFailure | None:
    if run is None or run.status != "failed":
        return None
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    return failure_for_error(
        run.error or "",
        restoration=isinstance(state.get("restoration_adaptation"), dict),
    )


def failure_for_error(raw_error: object, *, restoration: bool = False) -> GenerationFailure:
    error = str(raw_error).casefold()
    retryable = True
    if any(
        value in error
        for value in (
            "provider_auth_failed",
            "payment_required",
            "insufficient balance",
            "wallet_empty",
        )
    ):
        code = "provider_access"
        message = (
            "Сервис генерации временно недоступен: провайдер модели отклонил доступ. "
            "Требуется восстановить доступ к модели."
        )
        retryable = False
    elif "provider_timeout:" in error:
        code = "provider_unavailable"
        message = (
            "Провайдер модели не ответил вовремя. Автоматический повтор остановлен. "
            "Можно повторить исходный запрос."
        )
    elif "provider_unavailable" in error:
        code = "provider_unavailable"
        message = "Модель не ответила после повторных попыток. Попробуйте продолжить генерацию."
    elif "deadline" in error or "timeout" in error:
        code = "deadline"
        message = (
            "Генерация не успела завершить все проверки в отведённое время. "
            "Можно повторить исходный запрос."
        )
    elif "no source changes" in error:
        code = "no_changes"
        message = "Генератор не применил запрошенные изменения. Можно повторить исходный запрос."
    elif any(value in error for value in ("migration", "database", "preservation")):
        code = "data_contract"
        message = (
            "Изменение не прошло проверку сохранности данных. Непроверенная версия не принята."
        )
    else:
        code = "verification"
        message = "Приложение не прошло финальную проверку. Можно повторить исходный запрос."
    if restoration:
        retryable = False
        message += (
            " Для продолжения восстановления откройте историю версий и выберите проверенную версию."
        )
    return GenerationFailure(code=code, message=message, retryable=retryable)


def public_message_content(role: str, content: str, *, has_failure: bool = False) -> str:
    """Project legacy service-generated wrappers; never rewrite real/user text."""
    if role == "assistant" and content.lstrip().startswith(("[Ошибка:", "[Ошибка генерации:")):
        # Legacy writers could prepend a one-line wrapper to a real response.
        # Keep that response, but never expose an unclosed/truncated diagnostic.
        wrapper = re.match(r"\A\s*\[Ошибка(?: генерации)?:[^\r\n]*\](?:\r?\n|$)", content)
        suffix = content[wrapper.end() :] if wrapper else ""
        summary = "" if has_failure else failure_for_error(content).message
        return "\n".join(part for part in (summary, suffix) if part)
    return content
