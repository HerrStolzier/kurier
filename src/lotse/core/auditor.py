"""Self-audit system — catches duplicates, misclassifications, and anomalies.

The auditor reviews Lotse's own routing decisions and reports issues:
- Duplicate items (similar content routed to different locations)
- Potential misclassifications (re-classify and compare)
- Low-confidence items that may need manual review
- Orphaned files in review directory
- Missing destination files (routed file no longer at expected path)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from lotse.core.config import LotseConfig
from lotse.db.store import Store

logger = logging.getLogger(__name__)


@dataclass
class AuditIssue:
    """A single issue found during audit."""

    severity: str  # "high", "medium", "low"
    issue_type: str  # "duplicate", "misclassified", "low_confidence", "orphaned", "missing"
    message: str
    item_id: int | None = None
    related_id: int | None = None
    suggested_action: str = ""


@dataclass
class AuditReport:
    """Complete audit report."""

    issues: list[AuditIssue] = field(default_factory=list)
    items_checked: int = 0
    duplicates_found: int = 0
    misclassifications_found: int = 0
    low_confidence_count: int = 0
    orphaned_count: int = 0
    missing_count: int = 0

    @property
    def total_issues(self) -> int:
        return len(self.issues)

    @property
    def has_issues(self) -> bool:
        return len(self.issues) > 0


class Auditor:
    """Audits Lotse's routing decisions for quality."""

    def __init__(self, config: LotseConfig) -> None:
        self.config = config
        self.store = Store(config.database.path)

    def run_full_audit(
        self,
        check_duplicates: bool = True,
        check_confidence: bool = True,
        check_orphaned: bool = True,
        check_misclassified: bool = True,
        check_missing: bool = True,
    ) -> AuditReport:
        """Run a complete audit using thresholds from config."""
        report = AuditReport()
        audit_cfg = self.config.audit

        items = self.store.recent(limit=500)
        report.items_checked = len(items)

        if check_duplicates and self.store.vec_enabled:
            self._check_duplicates(items, report, audit_cfg.similarity_threshold)

        if check_confidence:
            self._check_low_confidence(items, report, audit_cfg.confidence_threshold)

        if check_orphaned:
            self._check_orphaned_review(report)

        if check_missing:
            self._check_missing_destinations(items, report)

        if check_misclassified and audit_cfg.reclassify_sample > 0:
            self._check_misclassified(items, report, audit_cfg.reclassify_sample)

        return report

    def _check_duplicates(
        self,
        items: list[dict],
        report: AuditReport,
        threshold: float,
    ) -> None:
        """Find items with very similar embeddings (likely duplicates)."""
        if not self.store.vec_enabled:
            return

        seen_pairs: set[tuple[int, int]] = set()

        for item in items:
            item_id = item["id"]

            try:
                vec_row = self.store._conn.execute(
                    "SELECT embedding FROM items_vec WHERE rowid = ?",
                    (item_id,),
                ).fetchone()
            except Exception as e:
                logger.debug("Failed to read embedding for item %d: %s", item_id, e)
                continue

            if not vec_row:
                continue

            embedding = vec_row[0]
            try:
                # sqlite-vec MATCH doesn't support AND filters,
                # so we fetch extra and filter in Python
                similar = self.store._conn.execute(
                    """SELECT rowid, distance
                       FROM items_vec
                       WHERE embedding MATCH ?
                       ORDER BY distance
                       LIMIT 6""",
                    (embedding,),
                ).fetchall()
            except Exception as e:
                logger.debug("Vector search failed for item %d: %s", item_id, e)
                continue

            for row in similar:
                other_id = row["rowid"]
                distance = row["distance"]

                # Skip self-match
                if other_id == item_id:
                    continue

                # sqlite-vec uses L2 distance; lower = more similar
                similarity = max(0, 1 - distance)

                if similarity >= threshold:
                    pair = (min(item_id, other_id), max(item_id, other_id))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    other = self.store._conn.execute(
                        "SELECT * FROM items WHERE id = ?", (other_id,)
                    ).fetchone()

                    if other:
                        other = dict(other)
                        report.issues.append(
                            AuditIssue(
                                severity="medium",
                                issue_type="duplicate",
                                message=(
                                    f"Possible duplicate: "
                                    f"'{item.get('summary', '')[:50]}' "
                                    f"and '{other.get('summary', '')[:50]}' "
                                    f"(similarity: {similarity:.0%})"
                                ),
                                item_id=item_id,
                                related_id=other_id,
                                suggested_action=("Review and delete the duplicate"),
                            )
                        )
                        report.duplicates_found += 1

    def _check_low_confidence(
        self,
        items: list[dict],
        report: AuditReport,
        threshold: float,
    ) -> None:
        """Flag items classified with low confidence."""
        for item in items:
            confidence = item.get("confidence", 0)
            if confidence < threshold and item.get("route_name") != "__review__":
                report.issues.append(
                    AuditIssue(
                        severity="low",
                        issue_type="low_confidence",
                        message=(
                            f"Low confidence ({confidence:.0%}): "
                            f"'{item.get('summary', '')[:60]}' "
                            f"classified as '{item.get('category', '?')}'"
                        ),
                        item_id=item["id"],
                        suggested_action=(f"Verify category '{item.get('category')}' is correct"),
                    )
                )
                report.low_confidence_count += 1

    def _check_orphaned_review(self, report: AuditReport) -> None:
        """Check for files stuck in the review directory."""
        review_dir = self.config.review_dir
        if not review_dir.exists():
            return

        orphaned = [f for f in review_dir.iterdir() if not f.name.startswith(".")]

        for f in orphaned:
            report.issues.append(
                AuditIssue(
                    severity="low",
                    issue_type="orphaned",
                    message=f"Unreviewed file: {f.name}",
                    suggested_action="Re-classify or manually sort this file",
                )
            )
            report.orphaned_count += 1

    def _check_missing_destinations(self, items: list[dict], report: AuditReport) -> None:
        """Check if routed files still exist at their destination."""
        for item in items:
            dest = item.get("destination", "")
            route = item.get("route_name", "")

            # Skip non-file routes (text input, webhooks, review)
            if not dest or route in ("__text__", "__review__", "__error__"):
                continue
            if dest.startswith("http"):
                continue

            dest_path = Path(dest)
            if not dest_path.exists():
                report.issues.append(
                    AuditIssue(
                        severity="high",
                        issue_type="missing",
                        message=(
                            f"File missing: '{item.get('summary', '')[:40]}' expected at {dest}"
                        ),
                        item_id=item["id"],
                        suggested_action=("File was moved or deleted after routing"),
                    )
                )
                report.missing_count += 1

    def _check_misclassified(
        self,
        items: list[dict],
        report: AuditReport,
        sample_size: int,
    ) -> None:
        """Re-classify a sample of items and compare with stored result.

        Only works if an LLM is available. Catches drift or errors.
        """
        try:
            from lotse.core.classifier import Classifier

            classifier = Classifier(self.config.llm)
        except Exception as e:
            logger.debug("Cannot re-classify (LLM unavailable): %s", e)
            return

        # Sample items that have stored content
        candidates = [
            i for i in items if i.get("content_text") and len(i.get("content_text", "")) > 20
        ][:sample_size]

        for item in candidates:
            try:
                new_result = classifier.classify(item["content_text"])
            except Exception as e:
                logger.debug("Re-classification failed for item %d: %s", item["id"], e)
                continue

            old_category = item.get("category", "")
            new_category = new_result.category

            if (
                old_category != new_category
                and new_result.confidence > 0.7
                and old_category != "unknown"
            ):
                report.issues.append(
                    AuditIssue(
                        severity="high",
                        issue_type="misclassified",
                        message=(
                            f"Was '{old_category}', now classified as "
                            f"'{new_category}' ({new_result.confidence:.0%}): "
                            f"'{item.get('summary', '')[:50]}'"
                        ),
                        item_id=item["id"],
                        suggested_action=(f"Move from '{old_category}' to '{new_category}'"),
                    )
                )
                report.misclassifications_found += 1
