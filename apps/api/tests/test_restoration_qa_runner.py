"""Испытательный прогон отката не имеет права выйти за пределы своей песочницы.

Этот пакет создаёт одноразовый синтетический проект и гоняет по нему настоящую
цепочку восстановления. Опасность здесь не в том, что проверка не пройдёт, а в
том, что она пройдёт НЕ ТАМ: подцепится к чужому проекту, к рабочей базе или
потратит оплаченную генерацию до того, как свидетель вообще достроен.

Поэтому границы проверяются раньше любого обращения наружу. Сначала доказано,
что цель — наша собственная одноразовая песочница, и только потом делается хоть
один запрос. Проверка «после подключения» здесь бесполезна: подключение к чужой
базе уже и есть то, чего нельзя допустить.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from yleum_api.ops.restoration_qa.contracts import CheckResult, QaManifest, QaScopeRefused

_SHA = "a" * 40
_DIGEST = "b" * 64


def _manifest(**overrides: object) -> QaManifest:
    payload: dict[str, object] = {
        "version": 1,
        "run_id": uuid4(),
        "release_sha": _SHA,
        "mode": "isolated",
        "project_id": uuid4(),
        "owner_id": uuid4(),
        "workspace_id": uuid4(),
        "fixture_digest": _DIGEST,
        "database_identity_digest": _DIGEST,
        "resource_manifest_digest": _DIGEST,
        "created_at": "2026-09-22T10:00:00Z",
        "expires_at": "2026-09-23T10:00:00Z",
    }
    payload.update(overrides)
    return QaManifest(**payload)  # type: ignore[arg-type]


def test_a_manifest_is_frozen_and_refuses_unknown_fields() -> None:
    manifest = _manifest()

    with pytest.raises(ValidationError):
        manifest.project_id = uuid4()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _manifest(project_slug="qa")


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_sha", "abc"),
        ("release_sha", "A" * 40),
        ("fixture_digest", "c" * 63),
        ("mode", "production"),
        ("version", 2),
    ],
)
def test_a_manifest_refuses_a_value_that_cannot_identify_anything(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _manifest(**{field: value})


def test_a_manifest_that_has_already_expired_is_refused() -> None:
    # Просроченный манифест указывает на песочницу, которой могло уже не быть.
    with pytest.raises(ValidationError):
        _manifest(created_at="2026-09-22T10:00:00Z", expires_at="2026-09-22T09:00:00Z")


def test_bootstrap_refuses_existing_foreign_project() -> None:
    """Песочница создаётся, а не занимается: чужой проект — это отказ."""
    from yleum_api.ops.restoration_qa.contracts import assert_bootstrap_scope

    existing = UUID("361b3326-97b4-4c73-94d5-c8d277a69dc6")

    with pytest.raises(QaScopeRefused) as caught:
        assert_bootstrap_scope(existing_project_ids={existing}, requested_project_id=existing)

    assert caught.value.reason_code == "foreign_project"


def test_bootstrap_accepts_only_a_project_it_created_itself() -> None:
    from yleum_api.ops.restoration_qa.contracts import assert_bootstrap_scope

    fresh = uuid4()

    assert_bootstrap_scope(existing_project_ids={uuid4(), uuid4()}, requested_project_id=fresh)


def test_runner_rejects_non_qa_database_before_connect() -> None:
    """Проверка границы обязана случиться ДО соединения, а не после.

    Подключение к чужой базе уже и есть то, чего нельзя допустить, поэтому
    проверять после connect бессмысленно.
    """
    from yleum_api.ops.restoration_qa.contracts import assert_qa_database

    manifest = _manifest()
    for dsn in (
        "postgresql://omnia:x@127.0.0.1:5432/omnia",
        "postgresql://omnia:x@170.168.72.200:5432/omnia_qa",
        "postgresql://postgres@db.example.com:5432/omnia-qa-" + manifest.run_id.hex,
    ):
        with pytest.raises(QaScopeRefused) as caught:
            assert_qa_database(manifest, dsn)

        assert caught.value.reason_code in {"foreign_database", "foreign_host"}


def test_the_runner_accepts_its_own_disposable_database() -> None:
    from yleum_api.ops.restoration_qa.contracts import assert_qa_database

    manifest = _manifest()
    dsn = f"postgresql://postgres@127.0.0.1:5432/omnia-qa-{manifest.run_id.hex}"

    assert_qa_database(manifest, dsn)


def test_the_refusal_never_carries_the_connection_string() -> None:
    from yleum_api.ops.restoration_qa.contracts import assert_qa_database

    manifest = _manifest()
    secret = "postgresql://omnia:hunter2@127.0.0.1:5432/omnia"

    with pytest.raises(QaScopeRefused) as caught:
        assert_qa_database(manifest, secret)

    rendered = str(caught.value)
    assert "hunter2" not in rendered and "postgresql://" not in rendered


def test_fixture_setup_creates_no_generation_runs() -> None:
    """Подготовка свидетеля не тратит оплаченную генерацию.

    Свидетель ещё не достроен: запускать настоящую генерацию на этом этапе —
    значит платить за прогон, который ничего не доказывает.
    """
    from yleum_api.ops.restoration_qa.contracts import assert_setup_spent_nothing

    assert_setup_spent_nothing(generation_runs=0, settlements=0)

    for runs, settlements, code in ((1, 0, "generation_spent"), (0, 1, "settlement_spent")):
        with pytest.raises(QaScopeRefused) as caught:
            assert_setup_spent_nothing(generation_runs=runs, settlements=settlements)

        assert caught.value.reason_code == code


def test_adaptive_requires_explicit_consent_profile() -> None:
    """Адаптацию запускает явное согласие, а не автоматическая политика."""
    from yleum_api.ops.restoration_qa.contracts import assert_adaptive_consent

    assert_adaptive_consent(profile="incompatible", explicit_prompt="верни экраны версии 5")

    for profile, prompt, code in (
        ("incompatible", "", "missing_prompt"),
        ("incompatible", "   ", "missing_prompt"),
        ("automatic", "верни экраны", "automatic_profile"),
    ):
        with pytest.raises(QaScopeRefused) as caught:
            assert_adaptive_consent(profile=profile, explicit_prompt=prompt)

        assert caught.value.reason_code == code


def test_a_check_result_cannot_report_a_verdict_without_evidence() -> None:
    ok = CheckResult(
        check_id="T03.bootstrap",
        status="PASS",
        observed_release_sha=_SHA,
        evidence_digest=_DIGEST,
        reason_code=None,
    )
    assert ok.status == "PASS"

    with pytest.raises(ValidationError):
        CheckResult(
            check_id="T03.bootstrap",
            status="FAIL",
            observed_release_sha=_SHA,
            evidence_digest=_DIGEST,
            reason_code=None,
        )


def test_a_not_run_check_is_never_mistaken_for_a_pass() -> None:
    for status in ("BLOCKED_ENV", "NOT_RUN"):
        result = CheckResult(
            check_id="T03.adaptive",
            status=status,  # type: ignore[arg-type]
            observed_release_sha=_SHA,
            evidence_digest=_DIGEST,
            reason_code="runtime_unavailable",
        )

        assert result.status != "PASS"
        assert result.reason_code


def test_the_fixture_sql_touches_only_its_own_tables() -> None:
    """Синтетика не смеет дотянуться до чужих таблиц.

    Свидетель ценен тем, что его состояние известно целиком. Любая строчка SQL,
    трогающая что-то помимо таблиц фикстуры, разрушает это: мы перестаём знать,
    что именно изменилось и чьё оно.
    """
    from yleum_api.ops.restoration_qa.fixtures import load_fixture

    bundle = load_fixture("incompatible")

    assert bundle.tables, "фикстура обязана объявлять свои таблицы"
    for statement in (bundle.schema_sql, bundle.seed_sql):
        for name in _referenced_tables(statement):
            assert name in bundle.tables, f"фикстура трогает чужую таблицу: {name}"


def _referenced_tables(sql: str) -> set[str]:
    import re

    pattern = re.compile(
        r'\b(?:CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|INSERT\s+INTO|UPDATE|DELETE\s+FROM|ALTER\s+TABLE)\s+"?([a-z_][a-z0-9_]*)"?',
        re.IGNORECASE,
    )
    return {match.group(1).lower() for match in pattern.finditer(sql)}


def test_the_fixture_rows_have_stable_identifiers() -> None:
    """Идентификаторы строк закреплены: свидетель должен быть сравним с собой."""
    from yleum_api.ops.restoration_qa.fixtures import load_fixture

    first = load_fixture("incompatible")
    second = load_fixture("incompatible")

    assert first.digest == second.digest
    assert first.row_ids == second.row_ids
    assert all(len(row_id) == 36 for row_id in first.row_ids)


def test_a_tampered_fixture_is_refused_not_used() -> None:
    """Отпечаток сверяется с объявленным: подменённая фикстура — отказ."""
    from yleum_api.ops.restoration_qa.fixtures import fixture_digest_mismatch

    assert fixture_digest_mismatch(declared="a" * 64, observed="a" * 64) is None
    assert fixture_digest_mismatch(declared="a" * 64, observed="b" * 64) == "fixture_tampered"


def test_the_two_versions_differ_where_the_profile_promises() -> None:
    """Профиль «несовместимо» обязан быть несовместимым на самом деле.

    Если версии отличаются только косметикой, вся цепочка доказательств
    проверяет не то: адаптация не понадобится, и «completed» ничего не скажет.
    """
    from yleum_api.ops.restoration_qa.fixtures import load_fixture

    bundle = load_fixture("incompatible")

    assert bundle.historical_files != bundle.current_files
    changed = {
        path
        for path in set(bundle.historical_files) | set(bundle.current_files)
        if bundle.historical_files.get(path) != bundle.current_files.get(path)
    }
    assert changed, "версии обязаны отличаться"
    # Отличие должно затрагивать работу с данными, а не только разметку.
    assert any(path.endswith((".sql", ".ts", ".tsx")) for path in changed)


def test_forbidden_keys_never_appear_in_fixture_output() -> None:
    """В отчёт не должны утечь секреты — даже из синтетики."""
    from yleum_api.ops.restoration_qa.fixtures import FORBIDDEN_OUTPUT_KEYS, load_fixture

    bundle = load_fixture("incompatible")
    rendered = (
        bundle.schema_sql
        + bundle.seed_sql
        + "".join(bundle.historical_files.values())
        + "".join(bundle.current_files.values())
    ).lower()

    assert FORBIDDEN_OUTPUT_KEYS
    for key in FORBIDDEN_OUTPUT_KEYS:
        assert key not in rendered, key


def test_polling_uses_a_monotonic_deadline_not_wall_clock() -> None:
    """Срок опроса не должен зависеть от перевода часов.

    Стенные часы могут прыгнуть назад — тогда прогон, считающий по ним, будет
    ждать дольше отпущенного и займёт песочницу, которая уже истекла.
    """
    from yleum_api.ops.restoration_qa.runner import PollBudget

    # Первый тик — момент создания бюджета: ждать начинают тогда, когда его завели.
    ticks = iter([100.0, 101.0, 130.0, 161.0])
    budget = PollBudget(seconds=60.0, clock=lambda: next(ticks))

    assert budget.remaining() == 59.0
    assert budget.remaining() == 30.0
    assert budget.expired() is True


def test_a_poll_never_sleeps_past_its_own_deadline() -> None:
    from yleum_api.ops.restoration_qa.runner import PollBudget

    ticks = iter([0.0, 55.0, 55.0])
    budget = PollBudget(seconds=60.0, clock=lambda: next(ticks))

    assert budget.next_sleep(interval=10.0) == 5.0, "последний сон не выходит за срок"


def test_a_resume_marker_survives_and_identifies_its_own_attempt() -> None:
    """Возобновление продолжает ТО ЖЕ, а не начинает похожее."""
    from yleum_api.ops.restoration_qa.runner import ResumeMarker

    marker = ResumeMarker(
        operation_id=uuid4(), idempotency_key="qa-adapt-once-0001", terminal=False
    )
    same = ResumeMarker(
        operation_id=marker.operation_id, idempotency_key=marker.idempotency_key, terminal=False
    )

    assert marker.resumes(same) is True
    assert marker.resumes(ResumeMarker(uuid4(), marker.idempotency_key, False)) is False
    assert marker.resumes(ResumeMarker(marker.operation_id, "qa-adapt-once-0002", False)) is False


def test_a_terminal_failure_is_never_resumed_under_the_old_identity() -> None:
    """Новый провал — новая попытка, иначе двa прогона сольются в один отчёт."""
    from yleum_api.ops.restoration_qa.runner import ResumeMarker

    finished = ResumeMarker(uuid4(), "qa-adapt-once-0001", terminal=True)
    retry = ResumeMarker(finished.operation_id, finished.idempotency_key, terminal=False)

    assert finished.resumes(retry) is False


def test_incompatibility_is_recorded_before_any_adaptive_prompt() -> None:
    """Порядок доказательства: сначала зафиксировали несовместимость, потом чинили.

    Если адаптацию запустить раньше, чем записан `needs_changes`, нечем будет
    показать, что она вообще требовалась.
    """
    from yleum_api.ops.restoration_qa.contracts import QaScopeRefused
    from yleum_api.ops.restoration_qa.runner import assert_adaptive_order

    assert_adaptive_order(needs_changes_recorded=True, automatic_policy_started_generation=False)

    with pytest.raises(QaScopeRefused) as caught:
        assert_adaptive_order(
            needs_changes_recorded=False, automatic_policy_started_generation=False
        )
    assert caught.value.reason_code == "missing_needs_changes"


def test_automatic_policy_must_not_have_started_a_generation() -> None:
    from yleum_api.ops.restoration_qa.contracts import QaScopeRefused
    from yleum_api.ops.restoration_qa.runner import assert_adaptive_order

    with pytest.raises(QaScopeRefused) as caught:
        assert_adaptive_order(
            needs_changes_recorded=True, automatic_policy_started_generation=True
        )

    assert caught.value.reason_code == "automatic_generation_started"
