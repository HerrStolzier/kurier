"""OCR support — extract text from PDFs and images.

Strategy: Try PyMuPDF native extraction first (fast, works for digital PDFs).
Fall back to Tesseract OCR only when native extraction yields little text.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Minimum chars to consider native extraction successful
MIN_TEXT_THRESHOLD = 50

# Supported file extensions
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


def is_ocr_candidate(file_path: Path) -> bool:
    """Check if a file can be processed by OCR."""
    return file_path.suffix.lower() in PDF_EXTENSIONS | IMAGE_EXTENSIONS


def extract_text(file_path: Path, languages: str = "deu+eng") -> str | None:
    """Extract text from PDF or image. Returns None if OCR is not available.

    Args:
        file_path: Path to PDF or image file
        languages: Tesseract language codes (e.g., "deu+eng")

    Returns:
        Extracted text, or None if no OCR libraries are installed
    """
    suffix = file_path.suffix.lower()

    if suffix in PDF_EXTENSIONS:
        return _extract_from_pdf(file_path, languages)
    elif suffix in IMAGE_EXTENSIONS:
        return _extract_from_image(file_path, languages)
    return None


def _extract_from_pdf(file_path: Path, languages: str) -> str | None:
    """Extract text from PDF — native first, OCR fallback."""
    try:
        import pymupdf
    except ImportError:
        logger.debug("pymupdf not installed, skipping PDF extraction")
        return None

    doc = pymupdf.open(str(file_path))  # type: ignore[no-untyped-call]
    pages_text: list[str] = []

    page: Any
    for page_num, page in enumerate(doc):  # type: ignore[arg-type]
        # Step 1: Try native text extraction (fast)
        text: str = page.get_text().strip()

        # Step 2: If too little text, try OCR
        if len(text) < MIN_TEXT_THRESHOLD:
            ocr_text = _ocr_pdf_page(page, languages)
            if ocr_text:
                text = ocr_text
                logger.debug("Page %d: used OCR (%d chars)", page_num + 1, len(text))
            else:
                logger.debug("Page %d: native extraction (%d chars)", page_num + 1, len(text))
        else:
            logger.debug("Page %d: native extraction (%d chars)", page_num + 1, len(text))

        if text:
            pages_text.append(text)

    doc.close()  # type: ignore[no-untyped-call]
    return "\n\n".join(pages_text) if pages_text else ""


def _ocr_pdf_page(page: Any, languages: str) -> str | None:
    """Run Tesseract OCR on a single PDF page via pytesseract."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None

    # Render page to image at 300 DPI for good OCR quality
    pix = page.get_pixmap(dpi=300)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    try:
        text = pytesseract.image_to_string(img, lang=languages)
        return text.strip() if text.strip() else None
    except Exception as e:
        logger.warning("OCR failed for page: %s", e)
        return None


def _extract_from_image(file_path: Path, languages: str) -> str | None:
    """Extract text from an image using Tesseract."""
    try:
        import pytesseract
    except ImportError:
        logger.debug("pytesseract not installed, skipping image OCR")
        return None

    try:
        text = pytesseract.image_to_string(str(file_path), lang=languages)
        return text.strip() if text.strip() else ""
    except Exception as e:
        logger.warning("Image OCR failed: %s", e)
        return None


def ocr_available() -> dict[str, bool]:
    """Check which OCR components are available."""
    result = {"pymupdf": False, "pytesseract": False, "tesseract_bin": False}

    try:
        import pymupdf  # noqa: F401

        result["pymupdf"] = True
    except ImportError:
        pass

    try:
        import pytesseract

        result["pytesseract"] = True
        # Check if tesseract binary is available
        pytesseract.get_tesseract_version()
        result["tesseract_bin"] = True
    except ImportError:
        pass
    except Exception:
        pass

    return result
