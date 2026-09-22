"""Ожидание и возобновление прогона: продолжать то же, а не похожее.

Прогон восстановления длинный, и его приходится ждать. Два способа испортить
такое ожидание встречаются чаще прочих.

Первый — считать время по стенным часам. Они могут прыгнуть назад, и тогда
прогон прождёт дольше отпущенного, заняв песочницу, срок которой уже вышел.
Поэтому срок здесь монотонный: он измеряет длительность, а не момент.

Второй — возобновить «похожее». Если после обрыва продолжить по одному лишь
идентификатору операции или по одному лишь ключу, два разных прогона сольются в
один отчёт, и станет невозможно сказать, что именно доказано. Возобновляется
только совпадение обоих, и только если прежняя попытка ещё не завершилась:
завершившийся провал — это новая попытка, а не продолжение старой.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from omnia_api.ops.restoration_qa.contracts import QaScopeRefused


@dataclass(frozen=True, slots=True)
class PollBudget:
    """Сколько ещё можно ждать — по длительности, а не по времени суток."""

    seconds: float
    clock: Callable[[], float] = time.monotonic
    _started: list[float] | None = None

    def __post_init__(self) -> None:
        # Первое обращение к часам задаёт точку отсчёта и больше не повторяется.
        object.__setattr__(self, "_started", [self.clock()])

    def remaining(self) -> float:
        started = (self._started or [0.0])[0]
        return max(0.0, self.seconds - (self.clock() - started))

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def next_sleep(self, *, interval: float) -> float:
        """Последний сон не выходит за срок: иначе проверка опоздает к сроку."""
        return max(0.0, min(interval, self.remaining()))


@dataclass(frozen=True, slots=True)
class ResumeMarker:
    """Метка попытки: чем именно она была и закончилась ли."""

    operation_id: UUID
    idempotency_key: str
    terminal: bool

    def resumes(self, other: ResumeMarker) -> bool:
        """Можно ли считать `other` продолжением этой попытки."""
        if self.terminal:
            # Завершившаяся попытка не продолжается — её результат уже записан.
            return False
        return (
            self.operation_id == other.operation_id
            and self.idempotency_key == other.idempotency_key
        )


def assert_adaptive_order(
    *,
    needs_changes_recorded: bool,
    automatic_policy_started_generation: bool,
) -> None:
    """Сначала зафиксировать несовместимость, потом чинить.

    Если запустить адаптацию раньше, чем записан отказ «нужны изменения», нечем
    будет показать, что она вообще требовалась, — и успешный итог перестанет
    что-либо доказывать. Автоматическая политика при этом не имеет права сама
    начать генерацию: адаптацию запускает явное согласие.
    """
    if not needs_changes_recorded:
        raise QaScopeRefused("missing_needs_changes")
    if automatic_policy_started_generation:
        raise QaScopeRefused("automatic_generation_started")


__all__ = ["PollBudget", "ResumeMarker", "assert_adaptive_order"]
