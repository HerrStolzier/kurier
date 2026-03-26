"""Tests for upload validation (src/arkiv/core/upload.py)."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest

from arkiv.core.upload import MAX_FILE_SIZE, validate_and_save


def _make_upload(
    filename: str | None = "test.txt",
    content: bytes = b"hello",
    content_type: str = "text/plain",
) -> MagicMock:
    """Build a mock UploadFile."""
    upload = MagicMock()
    upload.filename = filename
    upload.content_type = content_type

    # Simulate chunked async reads
    buf = BytesIO(content)

    async def _read(size: int = -1) -> bytes:
        if size == -1:
            return buf.read()
        return buf.read(size)

    upload.read = _read
    return upload


@pytest.mark.asyncio
async def test_valid_file_accepted(tmp_path: None) -> None:
    """A small valid file must be accepted and saved to a temp path."""
    upload = _make_upload("report.pdf", b"%PDF-1.4 content", "application/pdf")
    result = await validate_and_save(upload)
    try:
        assert result.exists()
        assert result.suffix == ".pdf"
        assert result.read_bytes() == b"%PDF-1.4 content"
    finally:
        result.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_file_over_limit_rejected() -> None:
    """A file exceeding 50 MB must be rejected with HTTP 413."""
    from fastapi import HTTPException

    oversized = b"x" * (MAX_FILE_SIZE + 1)
    upload = _make_upload("big.txt", oversized, "text/plain")
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save(upload)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_invalid_extension_rejected() -> None:
    """An unsupported file extension must be rejected with HTTP 400."""
    from fastapi import HTTPException

    upload = _make_upload("malware.exe", b"MZ binary", "application/octet-stream")
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save(upload)
    assert exc_info.value.status_code == 400
    assert "exe" in exc_info.value.detail


@pytest.mark.asyncio
async def test_missing_filename_rejected() -> None:
    """An upload without a filename must be rejected with HTTP 400."""
    from fastapi import HTTPException

    upload = _make_upload(filename=None, content=b"data", content_type="text/plain")
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save(upload)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_mime_type_rejected() -> None:
    """An upload with a disallowed MIME type must be rejected with HTTP 400."""
    from fastapi import HTTPException

    upload = _make_upload("script.txt", b"rm -rf /", "application/x-sh")
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_save(upload)
    assert exc_info.value.status_code == 400
