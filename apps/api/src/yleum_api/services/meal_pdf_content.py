"""Check the actual downloaded meal document inside an owned browser worker."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from io import BytesIO

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PAGES = 60
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_BROKEN_TEXT = re.compile(r"[\u25a0\u25a1\ufffd]")
_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)[\r\n]")


class MealPdfError(ValueError):
    """A fixed failure code. Document text is never part of the error."""


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def verify_meal_pdf_bytes(
    data: bytes,
    *,
    dishes: Sequence[str],
    ingredients: Sequence[str],
) -> int:
    from pypdf import PdfReader

    title = _normalized("Рацион на месяц")
    dish_labels = {_normalized(dish) for dish in dishes}
    ingredient_labels = {_normalized(ingredient) for ingredient in ingredients}
    labels = dish_labels | ingredient_labels
    if (
        not 3 <= len(dish_labels) == len(dishes) <= 12
        or not 2 <= len(ingredient_labels) <= len(ingredients) <= 60
        or title in labels
        or any(not _CYRILLIC.search(label) or len(label) > 160 for label in labels)
    ):
        raise MealPdfError("meal_pdf_invalid_expected_content")
    if not 0 < len(data) <= MAX_PDF_BYTES or not _HEADER.match(data):
        raise MealPdfError("meal_pdf_invalid_download")

    remaining = {
        label: re.compile(rf"(?<!\w){re.escape(label)}(?!\w)") for label in labels | {title}
    }
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= (page_count := len(reader.pages)) <= MAX_PAGES:
            raise MealPdfError("meal_pdf_invalid_pages")
        extracted = 0
        for page in reader.pages:
            raw = page.extract_text() or ""
            extracted += len(raw)
            if not raw.strip() or extracted > 250_000 or _BROKEN_TEXT.search(raw):
                raise MealPdfError("meal_pdf_unreadable_text")
            text = _normalized(raw)
            remaining = {
                label: phrase for label, phrase in remaining.items() if not phrase.search(text)
            }
    except MealPdfError:
        raise
    except Exception:
        # Third-party parsing errors can include untrusted document contents.
        raise MealPdfError("meal_pdf_unreadable_text") from None
    if remaining:
        raise MealPdfError("meal_pdf_missing_content")
    return page_count
