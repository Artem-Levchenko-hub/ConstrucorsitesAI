"""Public summary of a saved MAX snapshot, using only evidence from that candidate."""
from __future__ import annotations

import re

from yleum_api.services.generation.handoff_advice import normalize_handoff
from yleum_api.services.generation.recovery_acceptance import (
    Acceptance,
    AcceptanceSealV2,
    MealAcceptanceSeal,
)
from yleum_api.services.max_finalization import ProofBundle
from yleum_api.services.project_cell_proofs import ProofOutcome

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


def snapshot_outcome(
    proof: ProofBundle, acceptance: Acceptance | None, advice: object, *, owner_message: str,
    saw_failed_actions: bool = False,
) -> str:
    details = normalize_handoff(advice) or {}
    paragraphs = ["Версия приложения сохранена."]
    if proof.full_build is not None and proof.full_build.outcome == ProofOutcome.GREEN:
        paragraphs.append("Сборка прошла.")
    if proof.runtime is not None and proof.runtime.outcome == ProofOutcome.GREEN:
        paragraphs.append("Приложение запустилось в среде превью.")
    if isinstance(acceptance, MealAcceptanceSeal):
        paragraphs.append("В превью проверен сценарий работы с рационом.")
    elif isinstance(acceptance, AcceptanceSealV2):
        paragraphs.append("В превью проверен сценарий работы с заметками.")
    elif acceptance is not None:
        paragraphs.append("В превью выполнен проверочный сценарий этой версии.")
    else:
        action = details.get("primary_action")
        paragraphs.append(
            f"Осталось проверить в превью действие «{action.rstrip('.')}»."
            if action else "Осталось проверить основное действие в превью."
        )
    if saw_failed_actions:
        paragraphs.append(
            "Во время сборки приложение ответило ошибкой при проверке функций. "
            "Со стороны Yleum требуется повторная проверка этих действий в сохранённой версии."
        )
    if next_change := details.get("builder_next"):
        paragraphs.append(f"Следующая доработка по оценке разработчика — {next_change}")
    if quote := _original_commitment(details.get("owner_request_quote", ""), owner_message):
        # Display the owner's own commitment. Do not turn model-authored advice
        # into a new demand on the customer.
        paragraphs.append(f"От вас по исходному запросу — «{quote}».")
    return "\n\n".join(paragraphs)
