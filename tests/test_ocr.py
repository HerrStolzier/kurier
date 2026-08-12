"""Tests for OCR text extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from arkiv.core.ocr import (
    IMAGE_EXTENSIONS,
    MAX_OCR_PIXELS,
    PDF_EXTENSIONS,
    _ocr_pdf_page,
    _rendered_page_size,
    extract_text_with_status,
    is_ocr_candidate,
    ocr_available,
)


def test_is_ocr_candidate_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.touch()
    assert is_ocr_candidate(pdf)


def test_is_ocr_candidate_images(tmp_path: Path) -> None:
    for ext in [".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"]:
        img = tmp_path / f"test{ext}"
        img.touch()
        assert is_ocr_candidate(img), f"Should be OCR candidate: {ext}"


def test_is_not_ocr_candidate(tmp_path: Path) -> None:
    for ext in [".txt", ".md", ".json", ".csv"]:
        f = tmp_path / f"test{ext}"
        f.touch()
        assert not is_ocr_candidate(f), f"Should NOT be OCR candidate: {ext}"


def test_ocr_available_returns_dict() -> None:
    result = ocr_available()
    assert isinstance(result, dict)
    assert "pymupdf" in result
    assert "pytesseract" in result
    assert "tesseract_bin" in result


def test_extension_sets_are_disjoint() -> None:
    """PDF and image extensions should not overlap."""
    assert PDF_EXTENSIONS.isdisjoint(IMAGE_EXTENSIONS)


def test_rendered_page_size_estimates_pdf_pixels() -> None:
    class Rect:
        width = 72
        height = 144

    class Page:
        rect = Rect()

    assert _rendered_page_size(Page(), 300) == (300, 600, 180_000)


def test_ocr_pdf_page_skips_oversized_page_without_rendering() -> None:
    class Rect:
        width = 10_000
        height = 10_000

    class Page:
        rect = Rect()

        def get_pixmap(self, dpi: int) -> None:
            raise AssertionError("Oversized pages must not be rendered")

    assert MAX_OCR_PIXELS < 1_000_000_000
    assert _ocr_pdf_page(Page(), "deu+eng") is None


def test_grosse_seite_wird_heruntergerechnet_statt_uebersprungen() -> None:
    """Eine Seite ueber der Pixelgrenze bekommt eine kleinere Aufloesung.

    Vorher gab die OCR bei solchen Scans auf, der Text war leer und Kurier
    sortierte allein nach dem Dateinamen ein (2026-08-09).
    """
    from arkiv.core.ocr import MAX_OCR_PIXELS, MIN_OCR_DPI, PDF_OCR_DPI, _fitting_ocr_dpi

    class FakeRect:
        def __init__(self, w: float, h: float) -> None:
            self.width = w
            self.height = h

    class FakePage:
        def __init__(self, w: float, h: float) -> None:
            self.rect = FakeRect(w, h)

    # Normale A4-Seite: volle Aufloesung
    assert _fitting_ocr_dpi(FakePage(595, 842)) == PDF_OCR_DPI

    # Ueberformat wie im echten Fall (6091x8612 bei 300 DPI): reduziert, aber nutzbar
    gross = FakePage(595 * 2.5, 842 * 2.5)
    dpi = _fitting_ocr_dpi(gross)
    assert dpi is not None
    assert MIN_OCR_DPI <= dpi < PDF_OCR_DPI
    breite = int((gross.rect.width / 72) * dpi)
    hoehe = int((gross.rect.height / 72) * dpi)
    assert breite * hoehe <= MAX_OCR_PIXELS

    # Absurd gross: auch bei Mindestaufloesung nicht machbar
    assert _fitting_ocr_dpi(FakePage(60000, 60000)) is None


def test_pdf_reports_pages_beyond_extraction_limit(tmp_path: Path) -> None:
    import pymupdf

    path = tmp_path / "lang.pdf"
    document = pymupdf.open()
    for page_number in range(51):
        page = document.new_page()
        page.insert_text((72, 72), f"Seite {page_number}: " + "Vertragstext " * 8)
    document.save(path)
    document.close()

    text, is_partial = extract_text_with_status(path)

    assert text
    assert "Seite 49" in text
    assert "Seite 50" not in text
    assert is_partial is True


def test_pdf_reports_pages_beyond_ocr_limit(tmp_path: Path) -> None:
    import pymupdf

    path = tmp_path / "scans.pdf"
    document = pymupdf.open()
    for _ in range(11):
        document.new_page()
    document.save(path)
    document.close()

    with patch("arkiv.core.ocr._ocr_pdf_page", return_value="Gelesener Vertragstext"):
        text, is_partial = extract_text_with_status(path)

    assert text
    assert text.count("Gelesener Vertragstext") == 10
    assert is_partial is True
