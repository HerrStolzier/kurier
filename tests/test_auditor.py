"""Tests for the self-audit system."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lotse.core.auditor import AuditIssue, Auditor, AuditReport
from lotse.core.config import LotseConfig


def _make_embedding(seed: float = 0.0) -> bytes:
    """Create a fake 384-dim embedding for testing."""
    floats = [(seed + i * 0.001) for i in range(384)]
    return struct.pack(f"<{len(floats)}f", *floats)


@pytest.fixture
def config(tmp_path: Path) -> LotseConfig:
    return LotseConfig(
        database={"path": tmp_path / "test.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
    )


@pytest.fixture
def auditor(config: LotseConfig) -> Auditor:
    config.ensure_dirs()
    return Auditor(config)


def _add_item(auditor: Auditor, **kwargs) -> int:
    defaults = {
        "original_path": "/tmp/test.pdf",
        "destination": "/archive/test.pdf",
        "category": "rechnung",
        "confidence": 0.9,
        "summary": "Test item",
        "tags": [],
        "language": "de",
        "route_name": "archiv",
        "content_text": "",
        "embedding": None,
    }
    defaults.update(kwargs)
    return auditor.store.record_item(**defaults)


# --- AuditReport ---


def test_empty_report() -> None:
    report = AuditReport()
    assert report.total_issues == 0
    assert not report.has_issues


def test_report_with_issues() -> None:
    report = AuditReport(
        issues=[
            AuditIssue(severity="high", issue_type="missing", message="test"),
        ]
    )
    assert report.total_issues == 1
    assert report.has_issues


# --- Full Audit ---


def test_audit_empty_db(auditor: Auditor) -> None:
    report = auditor.run_full_audit(check_misclassified=False)
    assert report.items_checked == 0
    assert not report.has_issues


def test_audit_clean_items(auditor: Auditor) -> None:
    """Items with high confidence and no duplicates should pass."""
    _add_item(auditor, confidence=0.95, summary="Invoice A")
    _add_item(
        auditor,
        original_path="/tmp/article.md",
        destination="/articles/article.md",
        category="artikel",
        confidence=0.85,
        summary="Python tutorial",
        route_name="artikel",
    )

    report = auditor.run_full_audit(check_misclassified=False)
    # No low confidence, no orphaned, no missing
    low_conf = [i for i in report.issues if i.issue_type == "low_confidence"]
    assert len(low_conf) == 0


# --- Low Confidence ---


def test_flags_low_confidence(auditor: Auditor) -> None:
    _add_item(auditor, confidence=0.3, summary="Unsure item")

    report = auditor.run_full_audit(check_misclassified=False)
    assert report.low_confidence_count == 1
    assert report.issues[0].severity == "low"
    assert report.issues[0].issue_type == "low_confidence"


def test_skips_review_route_for_confidence(auditor: Auditor) -> None:
    """Items already in review should not be flagged for low confidence."""
    _add_item(auditor, confidence=0.2, route_name="__review__")

    report = auditor.run_full_audit(check_misclassified=False)
    assert report.low_confidence_count == 0


# --- Orphaned Review ---


def test_finds_orphaned_files(config: LotseConfig, auditor: Auditor) -> None:
    config.review_dir.mkdir(parents=True, exist_ok=True)
    (config.review_dir / "stuck_file.pdf").write_text("content")
    (config.review_dir / ".hidden").write_text("skip me")

    report = auditor.run_full_audit(check_misclassified=False)
    assert report.orphaned_count == 1  # Hidden file excluded
    assert "stuck_file.pdf" in report.issues[0].message


def test_no_orphaned_when_review_empty(auditor: Auditor) -> None:
    report = auditor.run_full_audit(check_misclassified=False)
    orphaned = [i for i in report.issues if i.issue_type == "orphaned"]
    assert len(orphaned) == 0


# --- Missing Destinations ---


def test_finds_missing_destination(auditor: Auditor) -> None:
    _add_item(
        auditor,
        destination="/nonexistent/path/invoice.pdf",
        route_name="archiv",
    )

    report = auditor.run_full_audit(check_misclassified=False)
    assert report.missing_count == 1
    assert report.issues[0].severity == "high"


def test_skips_text_and_webhook_routes(auditor: Auditor) -> None:
    """Text inputs and webhook destinations should not be checked."""
    _add_item(auditor, destination="", route_name="__text__")
    _add_item(
        auditor,
        destination="https://hooks.slack.com/xxx",
        route_name="slack",
    )

    report = auditor.run_full_audit(check_misclassified=False)
    missing = [i for i in report.issues if i.issue_type == "missing"]
    assert len(missing) == 0


def test_existing_destination_passes(auditor: Auditor, tmp_path: Path) -> None:
    dest = tmp_path / "archiv" / "invoice.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_text("content")

    _add_item(auditor, destination=str(dest), route_name="archiv")

    report = auditor.run_full_audit(check_misclassified=False)
    missing = [i for i in report.issues if i.issue_type == "missing"]
    assert len(missing) == 0


# --- Duplicates ---


def test_finds_duplicate_embeddings(auditor: Auditor) -> None:
    if not auditor.store.vec_enabled:
        pytest.skip("sqlite-vec not available")

    # Two items with nearly identical embeddings
    _add_item(
        auditor,
        summary="Telekom Rechnung März",
        embedding=_make_embedding(0.1),
    )
    _add_item(
        auditor,
        original_path="/tmp/test2.pdf",
        destination="/archive/test2.pdf",
        summary="Telekom Rechnung März Kopie",
        embedding=_make_embedding(0.1),  # Same embedding = duplicate
    )

    report = auditor.run_full_audit(check_misclassified=False)
    assert report.duplicates_found >= 1


def test_no_duplicate_for_different_embeddings(auditor: Auditor) -> None:
    if not auditor.store.vec_enabled:
        pytest.skip("sqlite-vec not available")

    _add_item(
        auditor,
        summary="Invoice",
        embedding=_make_embedding(0.0),
    )
    _add_item(
        auditor,
        original_path="/tmp/article.md",
        destination="/articles/article.md",
        summary="Python tutorial",
        category="artikel",
        embedding=_make_embedding(99.0),  # Very different
    )

    report = auditor.run_full_audit(check_misclassified=False)
    assert report.duplicates_found == 0


# --- Config Integration ---


def test_audit_uses_config_thresholds(tmp_path: Path) -> None:
    """Audit thresholds should come from config."""
    config = LotseConfig(
        database={"path": tmp_path / "test.db"},
        inbox_dir=tmp_path / "inbox",
        review_dir=tmp_path / "review",
        audit={"confidence_threshold": 0.99},  # Very strict
    )
    config.ensure_dirs()
    auditor = Auditor(config)

    _add_item(auditor, confidence=0.95)  # Would pass default 0.6

    report = auditor.run_full_audit(check_misclassified=False)
    assert report.low_confidence_count == 1  # Flagged due to strict threshold
