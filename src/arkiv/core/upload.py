"""Upload validation and safe file handling."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

ALLOWED_EXTENSIONS: set[str] = {
    ".pdf",
    ".txt",
    ".md",
    ".docx",
    ".doc",
    ".jpg",
    ".jpeg",
    ".png",
    ".tiff",
    ".bmp",
    ".eml",
    ".html",
    ".htm",
    ".csv",
    ".json",
    ".xml",
}

ALLOWED_MIME_PREFIXES: set[str] = {
    "text/",
    "application/pdf",
    "application/json",
    "application/xml",
    "image/",
    "message/rfc822",
    "application/vnd.openxmlformats",
    "application/msword",
}


async def validate_and_save(file: UploadFile) -> Path:
    """Validate upload and stream to temp file. Returns temp file path."""
    # Check filename and extension
    if not file.filename:
        raise HTTPException(400, "Dateiname fehlt")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Dateityp {suffix} nicht erlaubt")

    # Check content type
    content_type = file.content_type or ""
    if not any(content_type.startswith(p) for p in ALLOWED_MIME_PREFIXES):
        raise HTTPException(400, f"MIME-Typ {content_type} nicht erlaubt")

    # Stream to temp file with size limit
    stem = Path(file.filename).stem[:100]  # Truncate long names
    tmp_path: Path | None = None
    total_size = 0
    chunk_size = 8192
    try:
        with tempfile.NamedTemporaryFile(delete=False, prefix=f"{stem}_", suffix=suffix) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        413,
                        f"Datei zu groß (max {MAX_FILE_SIZE // 1024 // 1024} MB)",
                    )
                tmp.write(chunk)
    except HTTPException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Upload fehlgeschlagen: {exc}") from exc

    return tmp_path
