"""Source-change requirements and analysis-only intent shared across generation."""

from __future__ import annotations

import re
from typing import Literal

READONLY_SOURCE_TURN_KEY = "readonly_source_turn"


def _unquoted_request(prompt: str) -> str:
    return re.sub(
        r'```.*?```|`[^`]*`|«[^»]*»|“[^”]*”|"[^"]*"|(?<!\w)\'[^\']*\'',
        " ", prompt, flags=re.DOTALL,
    ).lower().strip()


def _without_polite_prefix(text: str) -> str:
    return re.sub(
        r"^(?:(?:пожалуйста|please)[,\s:]*|(?:can|could|would)\s+you\s+)+",
        "", text.strip(),
    ).strip()


def requested_source_edit(prompt: str) -> bool:
    """Recognize unquoted change commands, including after context/preservation clauses.

    This is a conservative write requirement, not an intent classifier: an
    explanation, investigation or continuation stays optional. An explicit
    desired product state can require a write without an imperative verb.
    """
    text = _unquoted_request(prompt)
    # Normalize every analysis object, including a second request after "и".
    text = re.sub(
        r"\bсделай\s+(?:(?:краткий|подробный)\s+)?(?:обзор|анализ|ревью)\b",
        "проанализируй",
        text,
    )
    for clause in re.split(r"[.!;\n]", text):
        if re.search(
            r"ничего\s+не\s+(?:меняй|изменяй|трогай)|"
            r"(?:do\s+not|don't)\s+(?:change|modify|edit)\s+"
            r"(?:anything\b|(?:files|code)(?=\s*(?:$|,)))",
            clause,
        ) and not re.search(r"\b(?:больше|кроме|else|except)\b", clause):
            return False
    commands = re.compile(
        r"\b(?:добавь|исправь|измени|поменяй|замени|удали|убери|устрани|реализуй|"
        r"сделай|обнови|доработай|почини|создай|собери|пересобери|разверни|опубликуй|"
        r"fix|add|change|modify|edit|replace|remove|delete|implement|update|"
        r"repair|create|build|rebuild|deploy|publish)\b"
    )
    # Nominal requests ("Нужно поле даты", "Нужна сортировка") are ordinary
    # change requests too. Keep their entire clause so commas/colons do not
    # detach the requirement from its analysis or preservation scope.
    for clause in re.split(r"[.!?;\n]", text):
        need = re.search(r"\bнуж(?:но|на|ен|ны)\s*,?\s*", clause)
        if need is None:
            continue
        prefix, requirement = clause[: need.start()], clause[need.end() :]
        if (
            re.search(
                r"\b(?:не|как|почему|объясни|расскажи|проверь|исследуй|проанализируй|"
                r"продолжи|обзор|анализ|ревью|review|inspect|continue|explain|investigate|"
                r"check|analy[sz]e|how|why|whether)\b",
                prefix,
            )
            or re.match(
                r"(?:только\s+)?(?:(?:провести|сделать|выполнить)\s+)?"
                r"(?:обзор|анализ|ревью|проверка|проверку|исследование|объяснение)\b",
                requirement,
            )
            or re.match(
                r"(?:только\s+)?(?:не|ли|объяснить|рассказать|проверить|исследовать|понять|"
                r"узнать|оценить|изучить|сохранить|сохранение)\b",
                requirement,
            )
            or re.search(
                r"^чтобы\b.*\bне\s+(?:менял\w*|изменял\w*|менять|изменять)\b",
                requirement,
            )
        ):
            continue
        if re.search(
            r"\b(?:интерфейс\w*|поле|поля|кноп\w*|дата|даты|дату|карточ\w*|"
            r"сортиров\w*|фильтр\w*|форм\w*|бейдж\w*|цвет\w*|сч[её]тчик\w*)\b",
            requirement,
        ):
            return True
    for clause in re.split(r"[.!?;,:\n]|\b(?:затем|then)\b", text):
        clause = re.sub(
            r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?",
            "",
            clause,
        )
        # Change verbs can describe an analysis request ("сделай обзор кода").
        # Only a separate imperative ("inspect ... and fix ...") exits that scope.
        if re.match(
            r"\s*(?:please\s+)?(?:review|inspect|continue|explain|investigate|check|"
            r"analy[sz]e|проверь|объясни|расскажи|исследуй|проанализируй|продолжи|"
            r"как|почему|how|why)\b",
            clause,
        ):
            edit_clause = re.search(r"\b(?:and|и)\s+(?=" + commands.pattern + ")", clause)
            if edit_clause is None:
                continue
            scope = clause[:edit_clause.start()]
            clause = clause[edit_clause.end() :]
            # English infinitives and UI labels can be coordinated inside the question.
            if re.search(
                r"\b(?:(?:how|when|whether)\s+to|(?:how|why|whether|if)\s+"
                r"(?:i|you|we|they)\s+(?:can|could|should|would|must|might|may))\b",
                scope,
            ) or (
                re.search(r"\b(?:the|these|those)\s+" + commands.pattern + r"\s+$", scope)
                and re.match(commands.pattern + r"\s+(?:actions?|buttons?|controls?)\b", clause)
            ):
                continue
        for match in commands.finditer(clause):
            prefix = clause[: match.start()]
            # English verbs are also nouns: require an actual leading imperative,
            # not a keyword somewhere in contextual prose. Russian imperatives
            # retain contextual matching (e.g. "В текущем приложении устрани ...").
            if match.group().isascii() and prefix.strip():
                continue
            if re.search(r"\b(?:не|not|never|don't)\s+(?:\w+\s+){0,2}$", prefix):
                continue
            if re.search(r"\b(?:как|почему|how|why|whether|explain|investigate)\b", prefix):
                continue
            return True
    return False


def explicitly_readonly_request(prompt: str) -> bool:
    return (
        re.fullmatch(
            r"ничего\s+не\s+(?:меняй|изменяй|трогай)"
            r"(?:\s+в\s+(?:приложении|проекте|коде|файлах|данных))?|"
            r"(?:do\s+not|don't)\s+(?:change|modify|edit)\s+"
            r"(?:anything|(?:(?:any|the)\s+)?(?:files|code))",
            _without_polite_prefix(_unquoted_request(prompt)).rstrip(".!"),
            flags=re.IGNORECASE,
        )
        is not None
    )


def _source_intent(prompt: str) -> Literal["edit", "readonly", "optional"]:
    """Classify one owner turn without turning a continuation into an analysis."""

    text = _without_polite_prefix(_unquoted_request(prompt))
    clauses = []
    for sentence in re.split(r"[.!;\n]", text):
        clause = _without_polite_prefix(sentence)
        prefix, _, explanation = clause.partition(",")
        if explicitly_readonly_request(prefix):
            # Preserving business data does not rule out application edits.
            if prefix.endswith(" в данных"):
                clause = _without_polite_prefix(explanation)
            elif re.match(
                r"(?:(?:только|просто|just|only)\s+)?(?:объясни|расскажи|explain)\b",
                explanation.strip(),
            ):
                clause = prefix
        if clause:
            clauses.append(clause)
    if not clauses:
        return "optional"
    text = ". ".join(clauses)
    # A bare continuation is not a new modification request. The established
    # product/checkpoint completion contract still governs what it may finish.
    if text.rstrip("!?") in {"продолжи", "продолжай", "continue"}:
        return "optional"
    # A whole later instruction can revoke an earlier change. A preservation
    # clause about data must not erase a separate request to change the UI.
    directive: bool | None = None
    for clause in clauses:
        if explicitly_readonly_request(clause):
            directive = False
        elif requested_source_edit(clause):
            directive = True
    if directive is not None:
        return "edit" if directive else "readonly"
    analysis = re.match(
        r"(?:(?:только|просто|just|only)\s+)?"
        r"(?:объясни|расскажи|проверь|исследуй|проанализируй|"
        r"review|inspect|explain|investigate|check|analy[sz]e)\b|"
        r"(?:сделай|выполни|дай)\s+(?:(?:краткий|подробный)\s+)?"
        r"(?:обзор|анализ|ревью)\b|"
        r"нуж(?:ен|на|но|ны)\s+(?:только\s+)?"
        r"(?:анализ|обзор|ревью|проверка|проверить|исследование|исследовать|"
        r"объяснить|рассказать)\b|"
        r"continue\s+(?:reviewing|inspecting|analy[sz]ing|investigating|checking)\b|"
        r"(?:продолжи|продолжай)\s+(?:исследование|анализ|обзор|ревью|проверку)\b|"
        r"this\s+is\s+(?:a\s+)?read-only\s+(?:review|analysis|inspection)\b",
        text,
    )
    question = text.endswith("?") and re.match(r"(?:как|почему|how|why)\b", text)
    return "readonly" if analysis or question else "edit"


def source_change_required(prompt: str) -> bool:
    """Require builder proof unless the owner asked for analysis or continuation."""
    return _source_intent(prompt) == "edit"


def readonly_source_request(prompt: str) -> bool:
    """Identify requests that can go directly to a source-only answer."""
    return _source_intent(prompt) == "readonly"


_HELP_OPENING = re.compile(
    r"^(?:(?:привет|здравствуйте|смотри(?:те)?|подскажи(?:те)?|пожалуйста|"
    r"hi|please|tell me)[\s,.!?:;—-]+)*"
    r"(?:почему|зачем|куда|откуда|где|как|когда|сколько|како[йея]|какая|какие|"
    r"что\s+(?:делать|дальше|происходит|случилось|нужно|значит|означает|с\s+)|"
    r"на\s+каком\s+этапе|в\s+ч[её]м|можно\s+ли|нужно\s+ли|"
    r"why|where|how|when|what|can\s+i|is\s+(?:my|the))\b"
)
_MISSING_OUTCOME = re.compile(
    r"\b(?:не\s+(?:могу|можем|уда[её]тся|получается|работа\w*|открыва\w*|"
    r"появ\w*|запуска\w*|загружа\w*|сохраня\w*|отправля\w*|"
    r"приш[её]л|пришл[аои]|вижу|видно|составил\w*|сохранил\w*|прош[еёл]\w*)|"
    r"ничего\s+не\s+\w+|(?:^|там\s+)пусто|^нет\s+\w+|"
    r"ошибка\s+(?:при|после|в)|"
    r"(?:выда[её]т|возвращает|показывает|появляется|возникает|вылетает)\s+ошибк[ау]|"
    r"(?:оста[её]тся|остал(?:ся|ась|ось|ись))\s+пуст\w*|пропал[аои]?|завис(?:ла|ло|ли)?|"
    r"(?:а|но)\s+[^.!?\n]{0,100}\b(?:нет|пусто)|"
    r"nothing\s+(?:happen\w*|show\w*)|(?:can['’]?t|cannot)\s+\w+|"
    r"(?:doesn['’]?t|didn['’]?t|won['’]?t)\s+\w+)\b"
)
_REQUESTED_BEHAVIOR = re.compile(
    r"\b(?:пусть|хочу\s*,?\s*чтобы|(?:нужно|надо)\s*,?\s*чтобы|"
    r"(?:подключи|отключи|настрой|запусти|перезапусти|добавь|исправь|почини|"
    r"измени|удали|создай|сохрани|убери|перенеси)(?:те)?|"
    r"(?:если|когда|when|if)\b[^.!?\n]{0,200}?[,—:–-]\s*"
    r"(?:покажи|показывай|выведи|выводи|скрой|открой|открывай|закрой|"
    r"show|display|open|hide|save|send|redirect)\b|"
    r"i\s+want\s+(?:you|the app|it)\s+to)\b"
)


def is_max_owner_question(prompt: str) -> bool:
    """Route owner questions and reports to project help before edit admission."""
    text = _without_polite_prefix(_unquoted_request(prompt))
    if not text or _REQUESTED_BEHAVIOR.search(text) or requested_source_edit(prompt):
        return False
    return bool(
        readonly_source_request(prompt)
        or _HELP_OPENING.match(text)
        or _MISSING_OUTCOME.search(text)
    )
