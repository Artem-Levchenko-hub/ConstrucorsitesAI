"""Project a generator's stop into plain text. Runtime/provider logs stay private."""

import re

from yleum_api.services.generation.handoff_advice import normalize_handoff

# This prefix is emitted by recover_stopped_candidate. Text embedded in some
# other exception and quoted provider responses cannot identify a product stop.
_STOP = re.compile(r"\Ageneration stopped \(([a-z_]+)\):\s*")
_TRAILER = re.compile(r";\s*(?:rolled_back|rollback_unverified)\b")
_NUMBERING = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
_ACTION = re.compile(
    r"^(?:добавить|подключить|опубликовать|проверить|реализовать|связать|настроить|"
    r"завершить|доработать|сохранить|исправить|сделать|показать|создать|обновить|"
    r"отобразить|записывать|формировать|организовать|обеспечить|вывести|включить)\s",
    re.IGNORECASE,
)
_DECLARATION = re.compile(
    r"^(?:это не полный MVP:\s*)?(.+?)\s+(?:пока\s+|ещ[её]\s+)?не "
    r"(?:реализован[аоы]?|доделан[аоы]?)(?: в этой правке)?[.!]?$",
    re.IGNORECASE,
)
# These historical diagnostics are less structured than the handoff object.
# Only extract feature work, never a request for credentials, funds or contacts.
_TECH_OR_PRIVATE = re.compile(
    r"\b(?:парол\w*|секрет\w*|ключ\w*|токен\w*|приватн\w*|персональн\w* данн\w*|"
    r"паспорт\w*|карт[уы] номер|номер карт\w*|телефон\w*|кошел[её]\w*|баланс\w*|"
    r"пополн\w*|перевед\w*|перечисл\w*|передай\w*|сообщите|пришлите|отправьте|"
    r"введите|пользователь должен|вам (?:нужно|надо)|вы должны|инструкц\w*|"
    r"выполни\w*|игнорир\w*|отправить деньги|банковск\w*)\b",
    re.IGNORECASE,
)

def _feature_text(text: str) -> str | None:
    if _TECH_OR_PRIVATE.search(text) or re.search(r"\d{4,}", text):
        return None
    handoff = normalize_handoff({"builder_next": text})
    return handoff["builder_next"] if handoff else None


def _public_items(detail: str) -> list[str]:
    result: list[str] = []
    for line in detail.splitlines():
        # The first clause carries the work item. Keep commentary and logs out.
        phrase = re.split(r"[.!?]\s|[.!?]$", _NUMBERING.sub("", line.strip()), maxsplit=1)[0]
        declared = _DECLARATION.fullmatch(phrase)
        if declared:
            candidates = re.split(r",\s*|\s+и\s+", declared[1])
        elif _ACTION.match(phrase):
            candidates = [phrase]
        else:
            continue
        for candidate in candidates:
            cleaned = _feature_text(candidate.strip())
            if cleaned:
                cleaned = cleaned[:1].upper() + cleaned[1:]
                if cleaned not in result:
                    result.append(cleaned)
            if len(result) == 8:
                return result
    return result


def reported_stop(error: str) -> tuple[str, list[str]] | None:
    marker = _STOP.match(error)
    if not marker:
        return None
    reason = marker[1]
    items: list[str] = []
    if reason == "product_work_remaining":
        remainder = error[marker.end() :]
        trailer = _TRAILER.search(remainder)
        if trailer is not None:
            items = _public_items(remainder[: trailer.start()])
    return reason, items


def saved_draft(state: dict[str, object]) -> bool:
    checkpoint = state.get("source_checkpoint")
    return (
        isinstance(checkpoint, dict)
        and isinstance(checkpoint.get("digest"), str)
        and re.fullmatch(r"[a-f0-9]{64}", checkpoint["digest"]) is not None
        and "baseline_sha" in checkpoint
        and (checkpoint["baseline_sha"] is None or (
            isinstance(checkpoint["baseline_sha"], str)
            and re.fullmatch(r"[a-f0-9]{40}", checkpoint["baseline_sha"]) is not None
        ))
    )
