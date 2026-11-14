"""Check a locally downloaded MAX meal PDF against witnessed Cyrillic content."""
from __future__ import annotations

import argparse
import logging
import os
import re
import stat
import sys
import unicodedata
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

TITLE = "Рацион на месяц"
MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PAGES = 60
MAX_TEXT_CHARS = 250_000
MAX_EXPECTED_LENGTH = 200
MAX_EXPECTED_ITEMS = 30
_PDF_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)[\r\n]")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_BROKEN_TEXT = re.compile(r"[\u25a0\u25a1\ufffd]")


class PdfVerificationError(ValueError):
    """A verifier failure that can be shown without disclosing inputs."""


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _expected(dishes: Sequence[str], ingredients: Sequence[str]) -> list[tuple[str, str]]:
    title = _normalized(TITLE)
    declared = [("заголовок", title)]
    for label, values in (("блюдо", dishes), ("ингредиент", ingredients)):
        if not 1 <= len(values) <= MAX_EXPECTED_ITEMS:
            raise PdfVerificationError("Укажите от 1 до 30 наблюдавшихся значений каждого вида.")
        seen: set[str] = set()
        for value in values:
            normalized = _normalized(value)
            if (len(value) > MAX_EXPECTED_LENGTH or not _CYRILLIC.search(normalized)
                    or _BROKEN_TEXT.search(normalized) or normalized == title):
                raise PdfVerificationError(
                    "Укажите названия блюд и ингредиентов кириллицей.",
                )
            if normalized not in seen:
                declared.append((label, normalized))
                seen.add(normalized)
    return declared


def _local_pdf(path: Path) -> bytes:
    # Reject UNC and device paths on Windows and slash-UNC spelling on POSIX.
    if path.drive.startswith("\\") or str(path).startswith(("\\\\", "//")):
        raise PdfVerificationError("Требуется локальный файл PDF.")
    if path.suffix.casefold() != ".pdf":
        raise PdfVerificationError("Требуется файл с расширением .pdf.")
    try:
        absolute = path.absolute()
        if os.name == "nt":
            import ctypes

            # A drive letter may be mapped to a network share.
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(absolute.anchor)
            if drive_type not in {2, 3, 5, 6}:  # Removable, fixed, CD-ROM, RAM disk.
                raise PdfVerificationError("Требуется локальный файл PDF.")
        for part in (*reversed(absolute.parents), absolute):
            entry = part.lstat()
            if stat.S_ISLNK(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & (
                stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise PdfVerificationError("Требуется локальный файл PDF без перенаправления.")
        if not stat.S_ISREG(entry.st_mode):
            raise PdfVerificationError("Требуется обычный локальный файл PDF.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with open(absolute, "rb", opener=lambda name, mode: os.open(name, mode | flags)) as source:
            current = os.fstat(source.fileno())
            if not stat.S_ISREG(current.st_mode):
                raise PdfVerificationError("Требуется обычный локальный файл PDF.")
            if current.st_size > MAX_PDF_BYTES:
                raise PdfVerificationError("Превышен допустимый размер PDF (8 МиБ).")
            data = source.read(MAX_PDF_BYTES + 1)
    except OSError:
        raise PdfVerificationError("Не удалось открыть локальный файл PDF.") from None
    if len(data) > MAX_PDF_BYTES:
        raise PdfVerificationError("Превышен допустимый размер PDF (8 МиБ).")
    if not _PDF_HEADER.match(data):
        raise PdfVerificationError("У файла отсутствует корректный заголовок PDF.")
    return data


def verify_pdf(path: Path, *, dishes: Sequence[str], ingredients: Sequence[str]) -> int:
    """Return the number of text-checked pages or raise a safe, fixed diagnostic."""
    expected = _expected(dishes, ingredients)
    data = _local_pdf(path)
    try:
        from pypdf import PdfReader
    except ImportError:
        raise PdfVerificationError("Установите pypdf в локальное окружение проверки.") from None

    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PdfVerificationError("Зашифрованный PDF не поддерживается.")
        pages = reader.pages
        page_count = len(pages)
        if not 1 <= page_count <= MAX_PAGES:
            raise PdfVerificationError("Допустимо от 1 до 60 страниц PDF.")
        remaining = [(label, re.compile(rf"(?<!\w){re.escape(value)}(?!\w)"))
                     for label, value in expected]
        text_chars = 0
        for page in pages:
            text = page.extract_text() or ""
            text_chars += len(text)
            if text_chars > MAX_TEXT_CHARS:
                raise PdfVerificationError("Превышен допустимый объём текста PDF.")
            if not text.strip() or _BROKEN_TEXT.search(text):
                raise PdfVerificationError("Страница пуста или содержит символы замены.")
            normalized = _normalized(text)
            remaining = [(label, phrase) for label, phrase in remaining
                         if not phrase.search(normalized)]
    except PdfVerificationError:
        raise
    except Exception:  # noqa: BLE001 -- parser exceptions can expose source PDF text.
        raise PdfVerificationError("PDF повреждён или текст не читается.") from None
    if remaining:
        raise PdfVerificationError(f"В PDF отсутствует ожидаемое значение ({remaining[0][0]}).")
    return page_count


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default can echo accidental URLs or private values.
        super().error("Укажите локальный PDF и значения --dish и --ingredient.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Local downloaded PDF path")
    parser.add_argument("--dish", required=True, action="append", help="Witnessed dish in Cyrillic")
    parser.add_argument(
        "--ingredient", required=True, action="append", help="Witnessed ingredient in Cyrillic",
    )
    args = parser.parse_args(argv)
    # Third-party parser warnings can contain excerpts from the source PDF.
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
    try:
        count = verify_pdf(args.pdf, dishes=args.dish, ingredients=args.ingredient)
    except PdfVerificationError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Текст проверен. Страниц: {count}. Проверьте отрисовку каждой страницы отдельно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
