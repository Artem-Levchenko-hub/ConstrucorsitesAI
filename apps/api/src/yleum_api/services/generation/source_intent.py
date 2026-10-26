"""Pure source-change requirements shared by both agent loops and finalization."""

from __future__ import annotations

import re


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
        r"сделай|обнови|доработай|"
        r"fix|add|change|modify|edit|replace|remove|delete|implement|update)\b"
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
            r"analy[sz]e|проверь|объясни|расскажи|исследуй|проанализируй|продолжи)\b",
            clause,
        ):
            edit_clause = re.search(r"\b(?:and|и)\s+(?=" + commands.pattern + ")", clause)
            if edit_clause is None or re.match(
                r"\s*(?:please\s+)?(?:explain|объясни|расскажи)\b",
                clause,
            ):
                continue
            clause = clause[edit_clause.end() :]
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


def source_change_required(prompt: str) -> bool:
    """One existing-product contract for early progress and final acceptance.

    Builder admission already selected a product turn. Unknown wording therefore
    cannot turn a clean old build into a successful modification. Explicit
    analysis/read-only requests remain non-writing; a real change command in a
    mixed request keeps its existing precedence.
    """
    text = _without_polite_prefix(_unquoted_request(prompt))
    # Preserving business data restricts side effects, not application edits.
    # Remove only that whole scoped instruction before classifying the rest.
    clauses = [
        _without_polite_prefix(clause)
        for clause in re.split(r"[.!;\n]", text)
        if clause.strip()
    ]
    clauses = [
        re.sub(
            r"^(ничего\s+не\s+(?:меняй|изменяй|трогай)),\s*"
            r"(?:только\s+)?(?:объясни|расскажи)\b.*$",
            r"\1", clause,
        )
        for clause in clauses
    ]
    clauses = [
        clause for clause in clauses
        if not (explicitly_readonly_request(clause) and clause.endswith(" в данных"))
    ]
    if not clauses:
        return False
    text = ". ".join(clauses)
    # A whole later instruction can revoke an earlier change. A preservation
    # clause about data must not erase a separate request to change the UI.
    directive: bool | None = None
    for clause in clauses:
        if explicitly_readonly_request(clause):
            directive = False
        elif requested_source_edit(clause):
            directive = True
    if directive is not None:
        return directive
    analysis = re.match(
        r"(?:объясни|расскажи|проверь|исследуй|проанализируй|"
        r"review|inspect|explain|investigate|check|analy[sz]e)\b|"
        r"(?:сделай|выполни|дай)\s+(?:(?:краткий|подробный)\s+)?"
        r"(?:обзор|анализ|ревью)\b|"
        r"нуж(?:ен|на|но|ны)\s+(?:только\s+)?"
        r"(?:анализ|обзор|ревью|проверка|проверить|исследование|исследовать|"
        r"объяснить|рассказать)\b",
        text,
    )
    return analysis is None
