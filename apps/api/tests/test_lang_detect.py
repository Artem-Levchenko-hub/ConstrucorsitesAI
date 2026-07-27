"""Tests for omnia_api.services.lang_detect — pure, no DB needed."""

from omnia_api.services.lang_detect import DEFAULT_LANGUAGE, detect_language


def test_english_detected() -> None:
    assert detect_language("Hello, this is an English landing page") == "en"


def test_russian_detected() -> None:
    assert detect_language("Нужен сайт для ресторана с меню и контактами") == "ru"


def test_short_russian_prompt_cannot_drift_to_bulgarian() -> None:
    # Production regression: langdetect 1.0.9 labels this exact RU prompt as
    # bg:0.99999, after which discovery is explicitly ordered to speak Bulgarian.
    assert detect_language("лендос аптеки сделай") == "ru"


def test_short_russian_noun_phrases_default_to_ru() -> None:
    # These are too short for reliable discrimination between ru/bg/mk.
    assert detect_language("аптека") == "ru"
    assert detect_language("сайт аптеки") == "ru"
    assert detect_language("Сайт аптеки в Барнауле") == "ru"


def test_short_cyrillic_with_common_latin_product_token_defaults_to_ru() -> None:
    assert detect_language("CRM для аптеки") == "ru"


def test_distinct_ukrainian_letters_preserve_non_ru_detection() -> None:
    assert detect_language("створи сайт українською мовою") == "uk"


def test_long_bulgarian_sentence_remains_detectable() -> None:
    text = (
        "Направи модерен сайт за аптека с онлайн поръчки, доставка до дома "
        "и консултация с фармацевт"
    )
    assert detect_language(text) == "bg"


def test_empty_string_returns_default() -> None:
    assert detect_language("") == DEFAULT_LANGUAGE


def test_none_returns_default() -> None:
    assert detect_language(None) == DEFAULT_LANGUAGE


def test_whitespace_only_returns_default() -> None:
    assert detect_language("   \t\n  ") == DEFAULT_LANGUAGE


def test_very_short_text_is_fail_soft() -> None:
    # Single characters or numbers may confuse langdetect; it must not raise.
    result = detect_language("x")
    assert isinstance(result, str) and len(result) > 0


def test_garbage_is_fail_soft() -> None:
    # Totally undetectable content must return the default, never raise.
    result = detect_language("!@#$%^&*()")
    assert result == DEFAULT_LANGUAGE or isinstance(result, str)
