"""Allowlisted failure summaries for durable message history; never echo logs."""

import re

from yleum_api.models.generation_run import GenerationRun
from yleum_api.schemas.message import GenerationFailure, GenerationFailureDiagnostics


def public_generation_failure(
    run: GenerationRun | None, *, agent_steps: object = None,
) -> GenerationFailure | None:
    if run is None or run.status != "failed":
        return None
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    failure = failure_for_error(
        run.error or "",
        restoration=isinstance(state.get("restoration_adaptation"), dict),
    )
    failure.diagnostics = _provider_diagnostics(run.error, agent_steps)
    return failure


def _provider_diagnostics(
    raw_error: object, agent_steps: object,
) -> GenerationFailureDiagnostics | None:
    """Project only facts belonging to this terminal provider failure.

    Old messages may have arbitrary log text. A prior transient provider error
    must not become the cause of an unrelated final build failure. The terminal
    marker establishes that link; numeric fallback supports historical runs.
    """
    error = raw_error if isinstance(raw_error, str) else ""
    marker = re.match(r"\Aprovider_http_([45]\d\d):", error, re.IGNORECASE)
    if marker is None and re.match(
        r"\A(?:provider_request_failed|provider_timeout|provider_unavailable|"
        r"provider_refusal_(?:access|funds|rate_limit)|billing_unavailable):",
        error, re.IGNORECASE,
    ) is None:
        return None
    from yleum_api.services.generation.recovery_trace import safe_recovery_trace

    upstream = int(marker.group(1)) if marker is not None else None
    fields: dict[str, object] = {"error_origin": "provider" if upstream is not None else "unknown"}
    if upstream is not None:
        fields["upstream_http_status"] = upstream
    if isinstance(agent_steps, list):
        for step in reversed(agent_steps[-1000:]):
            if not isinstance(step, dict):
                continue
            # Never walk past later work to borrow an earlier transient error.
            if step.get("kind") != "provider_error" or step.get("ok") is not False:
                break
            trace = step.get("recovery_trace")
            trace = trace if isinstance(trace, dict) else {}
            if step.get("will_retry", trace.get("will_retry")) is not False:
                break
            if upstream is None and error.casefold().startswith("provider_timeout:"):
                break  # A stale HTTP response cannot explain a later timeout.
            safe = safe_recovery_trace({**trace, **{
                key: step[key] for key in (
                    "error_code", "http_status", "upstream_http_status",
                    "provider_charge_ambiguous", "retry_safe",
                ) if key in step
            }})
            if upstream is not None and safe.get("upstream_http_status") != upstream:
                break  # Contradictory/stale evidence cannot decorate this failure.
            if safe.get("error_origin") in {"provider", "gateway", "unknown"}:
                fields["error_origin"] = safe["error_origin"]
            fields.update({key: safe[key] for key in (
                "error_code", "http_status", "upstream_http_status",
                "provider_charge_ambiguous", "retry_safe",
            ) if key in safe})
            break
    if fields.get("provider_charge_ambiguous") is True:
        fields["retry_safe"] = False
    return GenerationFailureDiagnostics.model_validate(fields)


def failure_for_error(raw_error: object, *, restoration: bool = False) -> GenerationFailure:
    error = str(raw_error).casefold()
    retryable = True
    # The gateway category reaches terminal/history errors as an internal marker.
    # Exact prefixes prevent arbitrary provider text from claiming a category.
    refusal = re.match(r"\Aprovider_refusal_(access|funds|rate_limit):", error)
    if error.startswith("platform_wallet_empty:"):
        code = "wallet_empty"
        retryable = False
        message = (
            "Недостаточно средств в кошельке Yleum. "
            "Пополните кошелёк Yleum и повторите запрос."
        )
    elif refusal is not None:
        category = refusal.group(1)
        code = "provider_unavailable" if category == "rate_limit" else "provider_access"
        retryable = False
        message = {
            "access": (
                "Провайдер модели отклонил доступ к запросу. Требуется проверить доступ "
                "к модели на стороне провайдера. Автоматический повтор остановлен."
            ),
            "funds": (
                "Провайдер модели сообщил о недостатке средств. Требуется пополнить баланс "
                "на стороне провайдера. Автоматический повтор остановлен."
            ),
            "rate_limit": (
                "Провайдер модели ограничил частоту запросов. Автоматический повтор остановлен. "
                "Требуется дождаться восстановления доступности модели."
            ),
        }[category]
    elif "behavior_readiness_unavailable:" in error:
        code = "runtime_unavailable"
        retryable = False
        message = (
            "Генерация остановлена до следующего вызова модели: недоступна обязательная "
            "проверка интерфейса. Требуется восстановить проверку на стороне сервиса."
        )
    elif "max_core_preparation_failed:" in error:
        code = "runtime_unavailable"
        message = (
            "Генерация не запускалась: не удалось подготовить среду MAX. "
            "Деньги за вызов модели не списаны. Повторите после восстановления среды."
        )
    elif re.search(r"\bprovider_http_(?:401|402|403):", error):
        code = "provider_access"
        retryable = False
        message = (
            "Провайдер модели отклонил запрос. Требуется проверить доступ и средства "
            "на стороне провайдера; автоматический повтор остановлен."
        )
    elif "provider_http_429:" in error:
        code = "provider_unavailable"
        retryable = False
        message = (
            "Провайдер модели вернул HTTP 429; точная причина не установлена. "
            "Автоматический повтор остановлен; требуется сверка запроса."
        )
    elif "provider_request_failed:" in error or re.search(r"\bprovider_http_[45]\d\d:", error):
        code = "provider_unavailable"
        retryable = False
        message = (
            "Не получен подтверждённый результат запроса к модели. "
            "Автоматический повтор остановлен; требуется проверить состояние запроса."
        )
    elif any(
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
