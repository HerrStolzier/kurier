"""Tests for OCR text extraction."""

from __future__ import annotations

from pathlib import Path

from arkiv.core.ocr import (
    IMAGE_EXTENSIONS,
    MAX_OCR_PIXELS,
    PDF_EXTENSIONS,
    _ocr_pdf_page,
    _rendered_page_size,
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
