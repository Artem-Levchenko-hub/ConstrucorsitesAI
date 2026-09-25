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
