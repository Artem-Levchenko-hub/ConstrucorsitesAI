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
