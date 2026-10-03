"""Описание провала должно сохранять то место, где провал, а не начало лога.

25.09.2026, прод, прогон c8d0c5f7 (адаптивный откат проекта b5c4c26d). Прогон
закончился отказом на финальной сборке, и владелец с агентом получили описание,
которое буквально противоречило случившемуся: в нём стояло «✓ Compiled
successfully in 2.3min», список собранных маршрутов и больше ничего. Настоящая
ошибка была в хвосте лога и не сохранилась.

Причина простая: описание режется по верхней границе, а режется ОТ НАЧАЛА. У
лога сборки начало — это шапка инструмента и перечень успешных шагов, а ошибка
всегда в конце. То есть чем длиннее лог, тем вернее в описание попадёт ровно то,
что не нужно.

Цена та же, что у остальных немых отказов этого дня: этим же текстом агенту
ставится задание на починку. Он получает «всё собралось» и не знает, что чинить.

Здесь закреплено, что у провалившейся проверки в описание попадает конец лога, а
начало — только как контекст, и что ограничение по размеру при этом соблюдается,
а вычищение секретов продолжает работать на всём, что сохраняется.
"""

from __future__ import annotations

from yleum_api.services.project_cell_proofs import _MAX_DETAIL_BYTES, failure_detail_excerpt

_HEAD = "> omnia-max-miniapp@0.1.0 build /workspace\n> next build\n"
_NOISE = "\n".join(f"  Route /api/thing-{i}   161 B   102 kB" for i in range(600))
_ERROR = (
    "Failed to compile.\n"
    "./src/app/api/restoration-probe/route.ts:42:11\n"
    "Type error: Property 'status' is missing in type 'NewLead'.\n"
)


def test_the_error_at_the_end_survives() -> None:
    """Главное: то, ради чего отказ и читают, не должно теряться."""
    excerpt = failure_detail_excerpt(_HEAD + _NOISE + "\n" + _ERROR)

    assert "Type error" in excerpt
    assert "restoration-probe/route.ts" in excerpt


def test_the_beginning_is_kept_as_context() -> None:
    """Без начала непонятно, какая вообще команда упала."""
    excerpt = failure_detail_excerpt(_HEAD + _NOISE + "\n" + _ERROR)

    assert "next build" in excerpt


def test_the_middle_is_the_part_that_goes() -> None:
    # Середина у лога сборки — перечень успешных шагов, она и не нужна.
    excerpt = failure_detail_excerpt(_HEAD + _NOISE + "\n" + _ERROR)

    assert "thing-300" not in excerpt
    assert len(excerpt.encode("utf-8")) <= _MAX_DETAIL_BYTES


def test_a_short_log_is_left_exactly_as_it_was() -> None:
    """Если всё помещается, ничего не режем и не переставляем."""
    short = _HEAD + _ERROR

    assert failure_detail_excerpt(short) == short


def test_the_cut_is_announced_not_silent() -> None:
    """Молчаливый обрыв читается как «больше ничего не было»."""
    excerpt = failure_detail_excerpt(_HEAD + _NOISE + "\n" + _ERROR)

    assert "…" in excerpt or "..." in excerpt


def test_the_recorder_applies_it_to_a_failure_and_not_to_a_pass() -> None:
    """Иначе правка осталась бы красивой функцией, которой никто не пользуется."""
    import inspect

    from yleum_api.services import project_cell_proofs as module

    source = inspect.getsource(module.record_proof_result)
    assert "failure_detail_excerpt(detail)" in source
    assert "ProofOutcome.GREEN" in source


def test_secrets_in_the_tail_are_still_redacted() -> None:
    """Хвост теперь сохраняется — значит чистка обязана работать и на нём."""
    from yleum_api.services.project_cell_proofs import _bounded_redacted_text

    leaky = _HEAD + _NOISE + "\npostgresql://user:hunter2@db:5432/app\n" + _ERROR
    stored = _bounded_redacted_text(failure_detail_excerpt(leaky), max_bytes=_MAX_DETAIL_BYTES)

    assert "hunter2" not in stored
    assert "Type error" in stored


def _middle_tap_failure():
    prefix = '[typecheck]\n> tsc --noEmit\n[targeted-test]\nTAP version 13\n'
    passing = ''.join(
        f'# Subtest: passing {i}\nok {i} - passing {i}\n  ---\n  duration_ms: 1\n  ...\n'
        for i in range(1, 80)
    )
    failure = (
        '# Subtest: Coffee filter keeps requested predicate\n'
        'not ok 80 - Coffee filter keeps requested predicate\n'
        "  ---\n  location: '/workspace/tests/qa-coffee.test.mjs:126:1'\n"
        "  failureType: 'testCodeFailure'\n  error: 'Coffee filter pipeline not found'\n"
        "  code: 'ERR_ASSERTION'\n  ...\n"
    )
    return prefix + passing + failure + passing + '# tests 159\n# pass 158\n# fail 1\n'


def test_middle_tap_failure_keeps_real_source_repair_classifier():
    from yleum_api.services.generation.agent_finalization import source_check_is_repairable

    excerpt = failure_detail_excerpt(_middle_tap_failure())
    assert source_check_is_repairable('fast_check', 'red', excerpt)
    assert 'Coffee filter pipeline not found' in excerpt
    assert '# fail 1' in excerpt
    assert len(excerpt.encode()) <= _MAX_DETAIL_BYTES


def test_middle_compiler_diagnostic_survives_later_noise():
    detail = ('[typecheck]\n' + _NOISE
              + '\nsrc/app/page.tsx(777,9): error TS2322: incompatible card type\n' + _NOISE)
    excerpt = failure_detail_excerpt(detail)
    assert 'page.tsx(777,9): error TS2322' in excerpt
    assert len(excerpt.encode()) <= _MAX_DETAIL_BYTES


def test_failure_excerpt_redacts_before_cuts_and_handles_utf8():
    detail = (_middle_tap_failure().replace(
        'Coffee filter pipeline not found', 'Ошибка 😀 ' * 1000,
    ) + '\nAUTH_SECRET=' + 'private-sentinel' * 600)
    excerpt = failure_detail_excerpt(detail)
    assert 'private-sentinel' not in excerpt
    assert len(excerpt.encode()) <= _MAX_DETAIL_BYTES
    assert "code: 'ERR_ASSERTION'" in excerpt


async def test_real_recorder_retains_middle_assertion_and_digest(monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from yleum_api.services import project_cell_proofs as module
    from yleum_api.services.generation.agent_finalization import source_check_is_repairable

    proof = SimpleNamespace(
        id=uuid4(), workspace_id=uuid4(), generation_run_id=uuid4(), fencing_epoch=1,
        **{key: 'a' * 64 for key in (
            'workspace_revision', 'dependency_digest', 'schema_data_digest',
            'cell_manifest_digest', 'base_image_digest', 'toolchain_digest',
            'build_config_digest',
        )}, resource_profile_version='test',
    )
    session = MagicMock()
    session.flush = AsyncMock()
    monkeypatch.setattr(module, 'find_proof_result', AsyncMock(return_value=None))
    result = await module.record_proof_result(
        session, proof=proof, dimension=module.ProofDimension.FAST_CHECK,
        outcome=module.ProofOutcome.RED, operation_id=uuid4(), artifact_ref=None,
        detail=_middle_tap_failure(),
    )
    assert source_check_is_repairable(result.dimension, result.outcome, result.redacted_detail)
    assert len(result.redacted_detail.encode()) <= _MAX_DETAIL_BYTES
    assert result.detail_digest == hashlib.sha256(result.redacted_detail.encode()).hexdigest()
    session.add.assert_called_once_with(result)
    session.flush.assert_awaited_once()


def test_diagnostic_excerpt_does_not_invent_trusted_source_section():
    from yleum_api.services.generation.agent_finalization import source_check_is_repairable

    log = _middle_tap_failure().replace('[targeted-test]', '[untrusted-command]')
    assert not source_check_is_repairable('fast_check', 'red', failure_detail_excerpt(log))


def test_small_byte_limits_remain_bounded():
    for limit in (1, 10, 31, 80, 256):
        assert len(failure_detail_excerpt(_middle_tap_failure(), max_bytes=limit).encode()) <= limit


def test_multiline_tap_assertion_message_is_kept():
    detail = _middle_tap_failure().replace(
        "error: 'Coffee filter pipeline not found'",
        'error: |-\n    Coffee filter pipeline not found\n    Expected requested control',
    )
    excerpt = failure_detail_excerpt(detail)
    assert 'Coffee filter pipeline not found' in excerpt
    assert 'Expected requested control' in excerpt
    assert "code: 'ERR_ASSERTION'" in excerpt
    assert len(excerpt.encode()) <= _MAX_DETAIL_BYTES


def test_long_multiline_tap_context_cannot_displace_classifier_fields():
    from yleum_api.services.generation.agent_finalization import source_check_is_repairable

    detail = _middle_tap_failure().replace(
        "error: 'Coffee filter pipeline not found'",
        'error: |-\n' + '\n'.join('    Expected ' + 'x' * 600 for _ in range(8)),
    )
    assert source_check_is_repairable('fast_check', 'red', detail)
    excerpt = failure_detail_excerpt(detail)
    assert source_check_is_repairable('fast_check', 'red', excerpt)
    assert "code: 'ERR_ASSERTION'" in excerpt
    assert len(excerpt.encode()) <= _MAX_DETAIL_BYTES


def test_tiny_secret_redaction_caps_never_expand():
    for limit in (1, 6, 7, 8, 9, 10):
        excerpt = failure_detail_excerpt('PASS=syntheticprivate\n' + 'noise' * 50,
                                         max_bytes=limit)
        assert 'syntheticprivate' not in excerpt
        assert len(excerpt.encode()) <= limit
