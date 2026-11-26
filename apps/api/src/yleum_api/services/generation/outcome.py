"""Public summary of a saved MAX snapshot, using only evidence from that candidate."""
from __future__ import annotations

import re
from collections.abc import Sequence

from yleum_api.services.generation.handoff_advice import normalize_handoff
from yleum_api.services.generation.recovery_acceptance import (
    Acceptance,
    AcceptanceSealV2,
    MealAcceptanceSeal,
)
from yleum_api.services.max_finalization import ProofBundle
from yleum_api.services.project_cell_proofs import ProofOutcome

_SNAPSHOT_SAVED = "Версия приложения сохранена."
_BUILD_PASSED = "Сборка прошла."
_PREVIEW_STARTED = "Приложение запустилось в среде превью."
_GENERIC_PREVIEW_CHECK = "Осталось проверить основное действие в превью."
_STANDARD_SNAPSHOT_OUTCOME = "\n\n".join((
    _SNAPSHOT_SAVED, _BUILD_PASSED, _PREVIEW_STARTED, _GENERIC_PREVIEW_CHECK,
))

# Only an explicit commitment can transfer a dependency to the app owner.
_OWNER_COMMITMENT = re.compile(
    r"(?:(?:я|мы)\s+(?:сам[аи]?\s+)?(?:предоставлю|предоставим|пришлю|пришлём|пришлем|"
    r"передам|передадим|загружу|загрузим)|(?:i|we) will (?:provide|send|upload|supply)) "
    r"[\w -]+", re.I,
)


def _original_commitment(quote: str, original: str) -> str:
    sentence = quote.rstrip(".")
    if not _OWNER_COMMITMENT.fullmatch(sentence):
        return ""
    if re.search(r"\b(?:если|когда|может|if|unless|when|not|не)\b", sentence, re.I):
        return ""
    statements = re.split(r"(?<=[.!?])\s+|[\r\n]+", original)
    return sentence if any(s.strip().rstrip(".") == sentence for s in statements) else ""


def advice_for_published_source(
    advice: dict[str, str] | None, *, authored: dict[str, str],
    base: dict[str, str], restored: dict[str, str] | None, published: dict[str, str],
) -> dict[str, str] | None:
    if not advice:
        return None
    if authored == published:
        return advice
    initial = restored if restored is not None else base
    return advice if {**initial, **authored} == published else None


def compact_snapshot_outcome(content: str) -> str:
    """Shorten the exact boilerplate shared by new and historical results."""
    return "Приложение готово." if content == _STANDARD_SNAPSHOT_OUTCOME else content


def snapshot_outcome(
    proof: ProofBundle, acceptance: Acceptance | None, advice: object, *, owner_message: str,
    saw_failed_actions: bool = False, changes: Sequence[str] | None = None,
) -> str:
    details = normalize_handoff(advice) or {}
    action_to_check = details.get("primary_action") if acceptance is None else None
    next_change = details.get("builder_next")
    owner_commitment = _original_commitment(details.get("owner_request_quote", ""), owner_message)
    build_passed = proof.full_build is not None and proof.full_build.outcome == ProofOutcome.GREEN
    preview_started = proof.runtime is not None and proof.runtime.outcome == ProofOutcome.GREEN
    checks = []
    if build_passed:
        checks.append(_BUILD_PASSED)
    if preview_started:
        checks.append(_PREVIEW_STARTED)
    if changes is None:
        paragraphs = [_SNAPSHOT_SAVED, *checks]
    else:
        title = "Версия сохранена."
        if build_passed and preview_started and not saw_failed_actions:
            more_work = action_to_check or next_change or owner_commitment
            title = "Версия собрана." if more_work else "Приложение готово."
        change_text = "Подробное описание изменений этой версии недоступно."
        if len(changes) == 1:
            change_text = changes[0]
        elif changes:
            change_text = "\n".join(f"- {change}" for change in changes)
        paragraphs = [
            title,
            "**Что изменилось**\n\n" + change_text,
        ]
        if checks:
            paragraphs.append(" ".join(checks))
    if isinstance(acceptance, MealAcceptanceSeal):
        paragraphs.append(
            "В превью открыли блюда, добавили ингредиент в список покупок и скачали PDF. "
            "После повторного открытия приложения отметка о покупке сохранилась."
        )
    elif isinstance(acceptance, AcceptanceSealV2):
        paragraphs.append(
            "В превью создали заметку, изменили её и открыли в новом окне. "
            "Проверили, что после удаления она исчезла из списка."
        )
    elif acceptance is not None:
        paragraphs.append("В превью выполнен проверочный сценарий этой версии.")
    elif action_to_check:
        paragraphs.append(f"Осталось проверить в превью действие «{action_to_check.rstrip('.')}».")
    elif changes is None:
        paragraphs.append(_GENERIC_PREVIEW_CHECK)
    if saw_failed_actions:
        paragraphs.append(
            "Во время сборки приложение ответило ошибкой при проверке функций. "
            "Со стороны Yleum требуется повторная проверка этих действий в сохранённой версии."
        )
    if next_change:
        paragraphs.append(f"Следующая доработка по оценке разработчика — {next_change}")
    if owner_commitment:
        # Display the owner's own commitment. Do not turn model-authored advice
        # into a new demand on the customer.
        paragraphs.append(f"От вас по исходному запросу — «{owner_commitment}».")
    return compact_snapshot_outcome("\n\n".join(paragraphs))
