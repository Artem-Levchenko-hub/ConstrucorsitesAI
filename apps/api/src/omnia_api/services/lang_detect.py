"""Detect the human language of a user's text → a short code (BCP-47-ish).

Pure + fail-soft: any detection failure returns the RU default (the business is
RU-first). Uses langdetect with a fixed seed for deterministic output in tests.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "ru"

# ``langdetect`` is unreliable on short, closely-related Cyrillic phrases:
# e.g. ``"лендос аптеки сделай"`` is classified as Bulgarian with >99%
# confidence, despite being ordinary Russian. Omnia is RU-first and an explicit
# ``users.default_language`` bypasses this detector, so ambiguous short Cyrillic
# input should fail toward Russian instead of persisting a wrong project language
# and ordering every downstream model to answer in it.
_SHORT_CYRILLIC_RU_MAX_LETTERS = 64
_CYRILLIC_START = ord("\u0400")
_CYRILLIC_END = ord("\u04ff")
# Letters that unambiguously signal a neighbouring Cyrillic language. Bulgarian
# has no positive-only letters, so a genuinely Bulgarian short prompt needs the
# explicit user language preference; a longer sentence remains detectable.
_DISTINCT_NON_RU_CYRILLIC = frozenset("іїєґўђјљњћџѓќѕ")


def _prefer_ru_for_short_cyrillic(text: str) -> bool:
    """Return True for short, predominantly Cyrillic, RU-ambiguous input."""
    letters = [char.lower() for char in text if char.isalpha()]
    if not letters or len(letters) > _SHORT_CYRILLIC_RU_MAX_LETTERS:
        return False
    cyrillic = [char for char in letters if _CYRILLIC_START <= ord(char) <= _CYRILLIC_END]
    if len(cyrillic) * 5 < len(letters) * 3:  # <60% Cyrillic: let detector decide
        return False
    return not any(char in _DISTINCT_NON_RU_CYRILLIC for char in cyrillic)


def detect_language(text: str | None) -> str:
    if not text or not text.strip():
        return DEFAULT_LANGUAGE
    if _prefer_ru_for_short_cyrillic(text):
        return DEFAULT_LANGUAGE
    try:
        from langdetect import DetectorFactory, detect  # type: ignore[import-untyped]

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
    return f"\nВажно: отвечай ТОЛЬКО на языке «{language}» (язык пользователя), не на русском.\n"
