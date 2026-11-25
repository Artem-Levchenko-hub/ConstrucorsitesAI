from types import SimpleNamespace

import pytest

from yleum_api.services.generation_failure import public_generation_failure


def test_execution_revision_is_recorded_without_overwriting_other_state(monkeypatch):
    from yleum_api.core.config import get_settings
    from yleum_api.services.generation_runs import note_generation_release

    monkeypatch.setenv("YLEUM_RELEASE_SHA", "a" * 40)
    get_settings.cache_clear()
    run = SimpleNamespace(agent_state={"preserved": 1})
    note_generation_release(run)
    assert run.agent_state == {"preserved": 1, "runtime_revision": "a" * 40}
    monkeypatch.delenv("YLEUM_RELEASE_SHA")
    get_settings.cache_clear()


def test_long_build_log_keeps_terminal_error_and_redacts_before_truncation():
    from yleum_api.services.agent_progress import bounded_redacted_diagnostic

    raw = "MAX_FINALIZATION_FAILED: compiled successfully\n" + "route table\n" * 1000
    raw += "DATABASE_URL=postgresql://user:TOP_SECRET@db:5432/app\nBUILD_RECEIPT_MISSING"
    detail = bounded_redacted_diagnostic(raw, max_bytes=2000)
    assert detail.startswith("MAX_FINALIZATION_FAILED")
    assert detail.endswith("BUILD_RECEIPT_MISSING")
    assert "TOP_SECRET" not in detail
    assert len(detail.encode()) <= 2000


@pytest.mark.parametrize("status", ["completed", "cancelled", "running"])
def test_nonfailure_has_no_error_card(status):
    assert public_generation_failure(SimpleNamespace(status=status, error="deadline")) is None


def test_provider_timeout_is_not_reported_as_a_failed_build_check():
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error="PROVIDER_TIMEOUT: synthetic-private-detail", agent_state={},
    ))
    assert failure.code == "provider_unavailable"
    assert "провайдер модели не ответил вовремя" in failure.message.casefold()
    assert "Автоматический повтор остановлен" in failure.message
    assert "synthetic-private" not in failure.model_dump_json()


@pytest.mark.parametrize("code,message", [
    ("protected_source_verification_unavailable", "не смог проверить"),
    ("protected_environment_recovery_required", "подтвердить восстановление"),
])
def test_protected_source_failure_distinguishes_read_outage_from_recovery(code, message):
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error=code + " private-secret", agent_state={},
    ))
    assert failure.code == "runtime_unavailable"
    assert failure.retryable is False
    assert message in failure.message
    assert "private-secret" not in failure.model_dump_json()


@pytest.mark.parametrize("prefix", [
    "protected_source_verification_unavailable", "protected_environment_recovery_required",
])
def test_similar_text_cannot_claim_a_classified_source_failure(prefix):
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error=prefix + "_extra", agent_state={},
    ))
    assert failure.code == "verification"


def test_platform_wallet_exhaustion_is_not_provider_funds_exhaustion():
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error="PLATFORM_WALLET_EMPTY: private-key", agent_state={},
    ))
    assert failure.code == "wallet_empty"
    assert failure.message == (
        "Недостаточно средств в кошельке Yleum. "
        "Пополните кошелёк Yleum и повторите запрос."
    )
    assert failure.retryable is False and "private-key" not in failure.model_dump_json()


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        ("generation deadline exceeded; phase=fast_check; secret=do-not-expose", "deadline", True),
        ("PROVIDER_AUTH_FAILED: key=do-not-expose", "provider_access", False),
        ("PROVIDER_UNAVAILABLE: url=do-not-expose", "provider_unavailable", True),
        ("edit produced no source changes", "no_changes", True),
        ("MAX migration contract unsafe DROP", "data_contract", True),
        ("unknown password=do-not-expose", "verification", True),
        (
            "MAX_CORE_PREPARATION_FAILED: timeout database token=do-not-expose",
            "runtime_unavailable",
            True,
        ),
    ],
)
def test_history_failure_is_allowlisted_and_never_echoes_raw_error(error, code, retryable):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error=error, agent_state={})
    )
    assert failure.code == code
    assert failure.retryable is retryable
    assert "do-not-expose" not in failure.model_dump_json()


def test_restoration_retry_must_use_reviewed_version_workflow():
    failure = public_generation_failure(
        SimpleNamespace(
            status="failed",
            error="deadline exceeded",
            agent_state={"restoration_adaptation": {"operation_id": "opaque"}},
        )
    )
    assert not failure.retryable
    assert "историю версий" in failure.message


@pytest.mark.parametrize("category,code,message", [
    ("access", "provider_access", "Провайдер модели отклонил доступ к запросу."),
    ("funds", "provider_access", "Провайдер модели сообщил о недостатке средств."),
    ("rate_limit", "provider_unavailable", "Провайдер модели ограничил частоту запросов."),
])
@pytest.mark.parametrize("restoration", [False, True])
def test_structured_provider_refusal_keeps_specific_safe_terminal_and_history_message(
    category, code, message, restoration,
):
    from yleum_api.services.generation_failure import failure_for_error

    raw = f"provider_refusal_{category}: private-key https://private.test/path"
    state = {"restoration_adaptation": {"operation_id": "opaque"}} if restoration else {}
    history = public_generation_failure(
        SimpleNamespace(status="failed", error=raw, agent_state=state),
    )
    terminal = failure_for_error(RuntimeError(raw), restoration=restoration)
    for failure in (history, terminal):
        assert failure.code == code
        assert failure.message.startswith(message)
        assert "Автоматический повтор остановлен" in failure.message
        assert not failure.retryable
        assert "private" not in failure.model_dump_json()
        assert ("историю версий" in failure.message) is restoration


@pytest.mark.parametrize("raw", [
    "provider_http_401: private-key",
    "provider_http_401: provider_refusal_funds: arbitrary provider text",
    "provider_http_401: insufficient balance private-key",
    "provider_http_401: provider_refusal_unknown: private-key",
])
def test_historical_http_401_without_structured_category_does_not_assert_lack_of_funds(raw):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error=raw, agent_state={}),
    )
    assert failure.code == "provider_access"
    assert "проверить доступ и средства" in failure.message.casefold()
    assert "недостат" not in failure.message.casefold()
    assert not failure.retryable
    assert "private" not in failure.model_dump_json()


@pytest.mark.parametrize("raw", [
    "provider_http_429: private-key",
    "provider_http_429: insufficient balance private-key",
    "provider_http_429: provider_refusal_rate_limit: private-key",
])
def test_unclassified_http_429_history_does_not_invent_a_cause(raw):
    failure = public_generation_failure(SimpleNamespace(status="failed", error=raw, agent_state={}))
    assert failure.code == "provider_unavailable" and failure.retryable is False
    assert "HTTP 429" in failure.message and "точная причина не установлена" in failure.message
    assert "сверк" in failure.message
    assert "частот" not in failure.message and "средств" not in failure.message
    assert "private" not in failure.model_dump_json()


@pytest.mark.parametrize("raw", [
    "provider_refusal_funds_extra: private-key",
    "provider_refusal_funds private-key",
    "upstream said provider_refusal_funds: private-key",
    "provider_refusal_unknown: private-key",
])
def test_provider_refusal_marker_requires_exact_trusted_prefix(raw):
    from yleum_api.services.generation_failure import failure_for_error

    failure = failure_for_error(raw)
    assert failure.code == "verification"
    assert "недостат" not in failure.message.casefold()
    assert "private" not in failure.model_dump_json()


@pytest.mark.parametrize(
    "wrapper", ["[Ошибка: password=NEVER_PUBLISH]", "[Ошибка генерации: token=NEVER_PUBLISH]"]
)
def test_legacy_service_error_is_projected_without_rewriting_user_or_partial_text(wrapper):
    from yleum_api.services.generation_failure import public_message_content

    assert "NEVER_PUBLISH" not in public_message_content("assistant", wrapper)
    assert public_message_content("assistant", wrapper, has_failure=True) == ""
    assert public_message_content("user", wrapper) == wrapper
    assert public_message_content("assistant", "real partial response") == "real partial response"


def test_empty_failed_build_never_persists_the_exception_secret():
    from yleum_api.services.generation.agent_messages import _failed_build_body

    assert "NEVER_PUBLISH" not in _failed_build_body(
        "", "PROVIDER_AUTH_FAILED: token=NEVER_PUBLISH"
    )


@pytest.mark.parametrize("has_failure", [True, False])
@pytest.mark.parametrize("prefix", ["Ошибка", "Ошибка генерации"])
def test_legacy_wrapper_keeps_real_response_suffix(prefix, has_failure):
    from yleum_api.services.generation_failure import public_message_content

    suffix = "Готовая часть ответа…\nСледующий абзац."
    content = f"[{prefix}: password=NEVER_PUBLISH]\n{suffix}"
    projected = public_message_content("assistant", content, has_failure=has_failure)
    assert projected.endswith(suffix)
    assert "NEVER_PUBLISH" not in projected
    if has_failure:
        assert projected == suffix


@pytest.mark.parametrize("has_failure", [True, False])
def test_truncated_legacy_wrapper_never_exposes_diagnostic_tail(has_failure):
    from yleum_api.services.generation_failure import public_message_content

    content = "[Ошибка: token=NEVER_PUBLISH\ntruncated secret=NEVER_PUBLISH"
    assert "NEVER_PUBLISH" not in public_message_content(
        "assistant", content, has_failure=has_failure
    )


def _stopped_run(reason, detail, *, state=None):
    return SimpleNamespace(
        status="failed", agent_state=state or {},
        error=f"generation stopped ({reason}): {detail}",
    )


def test_product_failure_lists_the_work_and_separately_confirms_the_saved_draft():
    work = [
        "Подключить реальную отправку продуктовой заявки в онлайн-супермаркет "
        "и оплату баллами после настройки магазина и его программы лояльности",
        "Опубликовать три тарифа в управляемом каталоге и подключить оплату "
        "и активацию подписки",
        "Проверить сквозные сценарии в MAX: выбор блюд, перенос в график, сохранение "
        "отметок после перезагрузки, экспорт PDF и историю заявок",
    ]
    run = _stopped_run(
        "product_work_remaining",
        f"{work[0]}.\n{work[1]}. Текущий интерфейс принимает только заявку.\n"
        f"{work[2]}.; rolled_back",
        state={"source_checkpoint": {"digest": "7" * 64, "baseline_sha": "a" * 40}},
    )
    failure = public_generation_failure(run)
    assert failure.code == "product_work_remaining"
    assert failure.remaining_work == work
    assert failure.draft_saved
    assert "rolled_back" not in failure.model_dump_json()
    assert "снимок" not in failure.message.casefold()


def test_older_explicit_feature_list_is_available_without_claiming_a_checkpoint():
    run = _stopped_run(
        "product_work_remaining",
        "Это не полный MVP: анкета, двухдневный план, отметки блюд из плана, покупки "
        "и личный профиль не реализованы в этой правке.; rollback_unverified: private_error",
        state={"source_checkpoint": {"digest": "not-a-digest", "baseline_sha": None}},
    )
    failure = public_generation_failure(run)
    assert failure.remaining_work == [
        "Анкета", "Двухдневный план", "Отметки блюд из плана", "Покупки", "Личный профиль",
    ]
    assert not failure.draft_saved
    assert "private_error" not in failure.model_dump_json()


def test_private_or_unrecognized_product_diagnostics_stay_private():
    run = _stopped_run(
        "product_work_remaining",
        "Подключить календарь.\nПодключить календарь.\n"
        "provider_http_401: payment_required\n"
        "Добавить https://example.org/private\n"
        "Настроить пароль редактора\nПодключить кабинет, пришлите номер карты\n"
        "Проверить private_trace(id)\nОтправьте деньги для продолжения.\n"
        "Проверить кабинет support@example.org; rollback_unverified: private_error",
    )
    failure = public_generation_failure(run)
    assert failure.code == "product_work_remaining"
    assert failure.remaining_work == ["Подключить календарь"]
    assert failure.diagnostics is None
    assert "private" not in failure.model_dump_json()


@pytest.mark.parametrize(("error", "code"), [
    ("generation stopped (max_steps): details; rolled_back", "step_limit"),
    ("generation stopped (max_steps_green): details; rolled_back", "step_limit"),
    ("generation stopped (max_steps_red): details; rolled_back", "step_limit"),
    ("generation stopped (max_segments): details; rollback_unverified", "step_limit"),
    ("generation stopped (source_deadline): details; rolled_back", "deadline"),
    ("generation deadline exceeded; phase=unknown", "deadline"),
    ("generation deadline exceeded during source repair", "deadline"),
    ("provider_http_413: private response", "request_too_large"),
    ("provider_http_422: request may be too large", "provider_unavailable"),
    ("old log: generation stopped (product_work_remaining): Подключить кабинет.; rolled_back",
     "verification"),
    ("generation stopped (product_work_remaining): Подключить кабинет. more private output",
     "product_work_remaining"),
])
def test_only_the_recorded_stop_explains_why_work_ended(error, code):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error=error, agent_state={})
    )
    assert failure.code == code
    assert not failure.remaining_work and not failure.draft_saved
    assert "private" not in failure.model_dump_json()
    assert ("превысил допустимый размер" in failure.message) == (code == "request_too_large")


def test_historical_inner_timeout_cannot_claim_exhaustion_of_the_whole_run():
    run = SimpleNamespace(status="failed", error="inner timeout", agent_state={})
    failure = public_generation_failure(run)
    assert failure.code == "deadline"
    assert failure.message == "Не удалось вовремя получить результат одного из шагов."
    run.error = "generation deadline exceeded; phase=fast_check"
    assert "Время этой попытки закончилось" in public_generation_failure(run).message


@pytest.mark.asyncio
async def test_saved_product_failure_has_the_same_safe_tasks_in_live_status_and_history(
    client, db_session,
):
    from tests.test_generation_runs import _owner_and_project
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.message import Message
    from yleum_api.services.generation_runs import reserve_generation_run

    owner, project = await _owner_and_project(db_session)
    run, _ = await reserve_generation_run(
        db_session, project_id=project.id, user_id=owner.id,
        idempotency_key="recorded-product-work", prompt="test",
    )
    message = Message(project_id=project.id, role="assistant", content="")
    db_session.add(message)
    await db_session.flush()
    run.assistant_message_id = message.id
    run.status = "failed"
    run.error = (
        "generation stopped (product_work_remaining): Проверить сохранение брони.; "
        "rollback_unverified: internal-stderr"
    )
    run.agent_state = {"source_checkpoint": {"digest": "4" * 64, "baseline_sha": None}}
    await db_session.commit()

    async def current_user():
        return owner

    app.dependency_overrides[get_current_user] = current_user
    try:
        status = await client.get(f"/api/projects/{project.id}/generation")
        history = await client.get(f"/api/projects/{project.id}/messages")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert status.status_code == history.status_code == 200
    entry = next(item for item in history.json() if item["id"] == str(message.id))
    failure = status.json()["failure"]
    assert failure == entry["generation_failure"]
    assert failure["remaining_work"] == ["Проверить сохранение брони"]
    assert failure["draft_saved"]
    assert not status.json()["acceptance"]["current_request_complete"]
    assert "internal-stderr" not in status.text + history.text
