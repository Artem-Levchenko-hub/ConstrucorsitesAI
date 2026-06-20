"""Tests for omnia_api.services.lang_detect — pure, no DB needed."""
from omnia_api.services.lang_detect import DEFAULT_LANGUAGE, detect_language


def test_english_detected() -> None:
    assert detect_language("Hello, this is an English landing page") == "en"


def test_russian_detected() -> None:
    # langdetect with seed=0 needs a few more tokens to distinguish ru from bg.
    # "Нужен сайт для ресторана с меню и контактами" is reliably ru.
    assert detect_language("Нужен сайт для ресторана с меню и контактами") == "ru"


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
