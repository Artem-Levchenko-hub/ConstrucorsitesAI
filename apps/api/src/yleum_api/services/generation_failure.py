"""Allowlisted failure summaries for durable message history; never echo logs."""

from yleum_api.models.generation_run import GenerationRun
from yleum_api.schemas.message import GenerationFailure


def public_generation_failure(run: GenerationRun | None) -> GenerationFailure | None:
    if run is None or run.status != "failed":
        return None
    error = (run.error or "").casefold()
    retryable = True
    if "provider_auth_failed" in error or "payment_required" in error:
        code = "provider_access"
        message = (
            "Сервис генерации временно недоступен: провайдер модели отклонил доступ. "
            "Требуется восстановить доступ к модели."
        )
        retryable = False
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
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    if isinstance(state.get("restoration_adaptation"), dict):
        retryable = False
        message += (
            " Для продолжения восстановления откройте историю версий и выберите проверенную версию."
        )
    return GenerationFailure(code=code, message=message, retryable=retryable)
