"""Standalone Cyrillic PDF checks using synthetic downloaded-document fixtures."""
from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import max_meal_pdf
from pypdf import PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

TITLE = "Рацион на месяц"
DISH = "Творожная запеканка"
INGREDIENT = "Овсяные хлопья"


def _font_path() -> Path:
    configured = os.environ.get("QA_CYRILLIC_FONT")
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    if configured:
        candidates.insert(0, Path(configured))
    return next((path for path in candidates if path.is_file()), candidates[0])


def write_fixture(path: Path, pages: list[list[str]]) -> None:
    font_name = "Meal Fixture Cyrillic"
    font = _font_path()
    if not font.is_file():
        raise unittest.SkipTest("Supply QA_CYRILLIC_FONT or install a Cyrillic TrueType font.")
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(font)))
    pdf = canvas.Canvas(str(path), pagesize=(420, 260), invariant=True)
    pdf.setTitle("Synthetic meal fixture")
    for page in pages:
        pdf.setFont(font_name, 16)
        for row, line in enumerate(page):
            pdf.drawString(30, 220 - row * 35, line)
        pdf.showPage()
    pdf.save()


class MealPdfTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "sensitive-local-name.pdf"

    def verify(self) -> int:
        return max_meal_pdf.verify_pdf(self.path, dishes=[DISH], ingredients=[INGREDIENT])

    def test_readable_cyrillic_nfc_and_whitespace(self) -> None:
        write_fixture(self.path, [[TITLE.upper(), DISH, "Овсяные    хлопья"], ["Яйцо"]])
        self.assertEqual(max_meal_pdf.verify_pdf(
            self.path, dishes=[DISH],
            ingredients=[INGREDIENT, unicodedata.normalize("NFD", "Яйцо")],
        ), 2)

    def test_each_expected_phrase_is_required(self) -> None:
        for pages in (
            [["Другой рацион", DISH, INGREDIENT]],
            [[TITLE, "Овсяная каша", INGREDIENT]],
            [[TITLE, DISH, "Пшеничные хлопья"]],
            [[TITLE, DISH, "Овсяные хлопьяные"]],
            [[TITLE, DISH, "Овсяные"], ["хлопья"]],
            [[TITLE, "????????", INGREDIENT]],
        ):
            with self.subTest(pages=pages):
                write_fixture(self.path, pages)
                with self.assertRaises(max_meal_pdf.PdfVerificationError):
                    self.verify()

    def test_blank_encrypted_and_mislabeled_files(self) -> None:
        for state in ("blank", "encrypted", "malformed", "html", "wrong extension"):
            with self.subTest(state=state):
                path = self.path
                if state in {"blank", "encrypted"}:
                    pdf = PdfWriter()
                    pdf.add_blank_page(width=140, height=140)
                    if state == "encrypted":
                        pdf.encrypt("fixture")
                    pdf.write(path)
                elif state == "malformed":
                    path.write_bytes(b"%PDF-1.7\ninvalid\n%%EOF\n")
                elif state == "html":
                    path.write_bytes(b"<html>sensitive-only</html>")
                else:
                    path = path.with_suffix(".txt")
                    write_fixture(path, [[TITLE, DISH, INGREDIENT]])
                with self.assertRaises(max_meal_pdf.PdfVerificationError):
                    max_meal_pdf.verify_pdf(path, dishes=[DISH], ingredients=[INGREDIENT])

    def test_resource_limits(self) -> None:
        with self.path.open("wb") as source:
            source.write(b"%PDF-1.7\n")
            source.truncate(max_meal_pdf.MAX_PDF_BYTES + 1)
        with self.assertRaisesRegex(max_meal_pdf.PdfVerificationError, "размер"):
            self.verify()
        pdf = PdfWriter()
        for _ in range(max_meal_pdf.MAX_PAGES + 1):
            pdf.add_blank_page(width=140, height=140)
        pdf.write(self.path)
        with self.assertRaisesRegex(max_meal_pdf.PdfVerificationError, "страниц"):
            self.verify()

    def test_witnesses_must_be_cyrillic_and_different_from_title(self) -> None:
        write_fixture(self.path, [[TITLE, DISH, INGREDIENT]])
        for dishes, ingredients in ((["Wrong"], [INGREDIENT]), ([TITLE], [INGREDIENT]),
                                   ([DISH], [])):
            with (
                self.subTest(dishes=dishes, ingredients=ingredients),
                self.assertRaises(max_meal_pdf.PdfVerificationError),
            ):
                max_meal_pdf.verify_pdf(self.path, dishes=dishes, ingredients=ingredients)

    def test_a_dish_and_an_ingredient_can_have_the_same_name(self) -> None:
        write_fixture(self.path, [[TITLE, "Яйцо", DISH, INGREDIENT]])
        self.assertEqual(max_meal_pdf.verify_pdf(
            self.path, dishes=["Яйцо", DISH], ingredients=["Яйцо", INGREDIENT, "яйцо"],
        ), 1)

    def test_network_targets_are_rejected_before_file_access(self) -> None:
        for path in (Path(r"\\example.invalid\remote\export.pdf"),
                     Path("//example.invalid/remote/export.pdf")):
            with self.subTest(path=path), patch.object(Path, "lstat") as lookup:
                with self.assertRaisesRegex(max_meal_pdf.PdfVerificationError, "локальный файл"):
                    max_meal_pdf.verify_pdf(path, dishes=[DISH], ingredients=[INGREDIENT])
                lookup.assert_not_called()

    def test_symlink_target_is_rejected(self) -> None:
        write_fixture(self.path, [[TITLE, DISH, INGREDIENT]])
        alias = self.path.with_name("alias.pdf")
        try:
            alias.symlink_to(self.path)
        except OSError:
            self.skipTest("This host does not allow creating a symbolic link.")
        with self.assertRaisesRegex(max_meal_pdf.PdfVerificationError, "без перенаправления"):
            max_meal_pdf.verify_pdf(alias, dishes=[DISH], ingredients=[INGREDIENT])

    def test_directory_named_pdf_is_rejected(self) -> None:
        self.path.mkdir()
        with self.assertRaisesRegex(max_meal_pdf.PdfVerificationError, "обычный локальный файл"):
            self.verify()

    def test_cli_syntax_error_does_not_echo_unexpected_private_arguments(self) -> None:
        result = self.run_cli("unexpected-sensitive-argument")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("unexpected-sensitive-argument", result.stdout + result.stderr)

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(Path(max_meal_pdf.__file__)), str(self.path),
             "--dish", DISH, "--ingredient", INGREDIENT, *arguments],
            capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def test_cli_fails_without_echoing_path_values_or_document(self) -> None:
        write_fixture(self.path, [[TITLE, DISH, "Конфиденциальная строка"]])
        check = self.run_cli()
        self.assertNotEqual(check.returncode, 0)
        output = check.stdout + check.stderr
        self.assertNotIn(self.path.name, output)
        self.assertNotIn(DISH, output)
        self.assertNotIn(INGREDIENT, output)
        self.assertNotIn("Конфиденциальная строка", output)


if __name__ == "__main__":
    unittest.main()
