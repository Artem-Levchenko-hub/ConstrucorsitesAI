"""Detect the human language of a user's text → a short code (BCP-47-ish).

Pure + fail-soft: any detection failure returns the RU default (the business is
RU-first). Uses langdetect with a fixed seed for deterministic output in tests.
"""
from __future__ import annotations

DEFAULT_LANGUAGE = "ru"


def detect_language(text: str | None) -> str:
    if not text or not text.strip():
        return DEFAULT_LANGUAGE
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        code = detect(text)
        return code or DEFAULT_LANGUAGE
    except Exception:
        return DEFAULT_LANGUAGE


def _reply_language_line(language: str) -> str:
    """Return a short system-prompt suffix that instructs the model to reply in
    ``language`` instead of Russian.

    Returns an empty string for RU (the default) so system prompts for Russian
    projects are byte-for-byte identical to the pre-i18n baseline — no diff,
    no regression risk. For any other language the returned string is appended
    to the existing system prompt.
    """
    lang = (language or "ru").strip().lower()
    if lang.startswith("ru"):
        return ""  # RU default — system prompt unchanged
    return (
        f"\nВажно: отвечай ТОЛЬКО на языке «{language}» (язык пользователя),"
        " не на русском.\n"
    )
