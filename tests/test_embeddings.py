"""Tests for the embedding engine."""

from __future__ import annotations

import struct

from lotse.core.embeddings import (
    EMBEDDING_DIM,
    _bytes_to_float_list,
    _float_list_to_bytes,
)


def test_float_roundtrip() -> None:
    """Test that float list → bytes → float list is lossless."""
    original = [0.1, 0.2, 0.3, -0.5, 1.0]
    packed = _float_list_to_bytes(original)
    unpacked = _bytes_to_float_list(packed)

    assert len(unpacked) == len(original)
    for a, b in zip(original, unpacked):
        assert abs(a - b) < 1e-6


def test_bytes_size() -> None:
    """Verify packed bytes have correct size (4 bytes per float32)."""
    floats = [0.0] * EMBEDDING_DIM
    packed = _float_list_to_bytes(floats)
    assert len(packed) == EMBEDDING_DIM * 4


def test_empty_list() -> None:
    packed = _float_list_to_bytes([])
    assert packed == b""
    assert _bytes_to_float_list(b"") == []


def test_packing_format_is_little_endian_float32() -> None:
    """Ensure format matches what sqlite-vec expects."""
    val = [1.0]
    packed = _float_list_to_bytes(val)
    # IEEE 754 little-endian float32 for 1.0 is 0x3F800000
    assert packed == struct.pack("<f", 1.0)
