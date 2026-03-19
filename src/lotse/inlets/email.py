"""Email inlet — process emails from IMAP, .eml, or .mbox files."""

from __future__ import annotations

import imaplib
import logging
import mailbox
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from mailbox import mboxMessage
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParsedEmail:
    """Structured data extracted from an email."""

    subject: str
    sender: str
    to: str
    body: str
    attachments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text_for_classification(self) -> str:
        """Combine email metadata + body for LLM classification."""
        parts = [
            f"Subject: {self.subject}",
            f"From: {self.sender}",
            f"To: {self.to}",
            "",
            self.body[:4000],
        ]
        return "\n".join(parts)


def parse_eml(file_path: Path) -> ParsedEmail:
    """Parse a single .eml file."""
    with open(file_path, "rb") as f:
        msg = BytesParser(policy=default).parse(f)
    return _extract_email(msg)


def parse_mbox(file_path: Path) -> list[ParsedEmail]:
    """Parse all messages from a .mbox file."""
    mbox = mailbox.mbox(str(file_path))
    results = []
    for msg in mbox:
        try:
            results.append(_extract_email(msg))
        except Exception as e:
            logger.warning("Failed to parse mbox message: %s", e)
    return results


def fetch_imap(
    host: str,
    username: str,
    password: str,
    folder: str = "INBOX",
    limit: int = 50,
    mark_read: bool = True,
) -> list[ParsedEmail]:
    """Fetch unread emails from an IMAP server.

    Args:
        host: IMAP server hostname (e.g., imap.gmail.com)
        username: Email address
        password: Password or app password
        folder: Mailbox folder to check
        limit: Maximum number of emails to fetch
        mark_read: Whether to mark fetched emails as read

    Returns:
        List of parsed emails
    """
    results = []

    try:
        server = imaplib.IMAP4_SSL(host, 993)
        server.login(username, password)
        server.select(folder, readonly=not mark_read)

        _, msg_ids = server.search(None, "UNSEEN")
        ids = msg_ids[0].split()[:limit]

        logger.info("Found %d unread emails in %s", len(ids), folder)

        for msg_id in ids:
            _, data = server.fetch(msg_id, "(RFC822)")
            if data[0] is None:
                continue

            raw = data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = BytesParser(policy=default).parsebytes(raw)
            parsed = _extract_email(msg)
            results.append(parsed)

            if mark_read:
                server.store(msg_id, "+FLAGS", "\\Seen")

        server.close()
        server.logout()

    except imaplib.IMAP4.error as e:
        logger.error("IMAP error: %s", e)
        raise
    except Exception as e:
        logger.error("Email fetch failed: %s", e)
        raise

    return results


def save_attachments(parsed_email: ParsedEmail, dest_dir: Path) -> list[Path]:
    """Save email attachments to disk. Returns list of saved file paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    for att in parsed_email.attachments:
        filename = att.get("filename", "attachment")
        # Sanitize filename
        safe_name = "".join(
            c if c.isalnum() or c in ".-_" else "_" for c in filename
        )
        if not safe_name:
            safe_name = "attachment"

        dest_path = dest_dir / safe_name
        # Handle collisions
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        dest_path.write_bytes(att["data"])
        saved.append(dest_path)
        logger.info("Saved attachment: %s", dest_path.name)

    return saved


def _extract_email(msg: EmailMessage | mboxMessage) -> ParsedEmail:
    """Extract structured data from an email.message object."""
    body = _get_body(msg)
    attachments = _get_attachments(msg)

    return ParsedEmail(
        subject=str(msg.get("subject", "")),
        sender=str(msg.get("from", "")),
        to=str(msg.get("to", "")),
        body=body,
        attachments=attachments,
    )


def _get_body(msg: EmailMessage | mboxMessage) -> str:
    """Extract plain text body from email, falling back to HTML."""
    # Try plain text first
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                try:
                    return str(part.get_content())  # type: ignore[union-attr]
                except Exception:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return bytes(payload).decode("utf-8", errors="replace")
        # Fall back to HTML
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html: str = str(part.get_content())  # type: ignore[union-attr]
                except Exception:
                    payload = part.get_payload(decode=True)
                    html = (
                        payload.decode("utf-8", errors="replace")
                        if isinstance(payload, bytes)
                        else ""
                    )
                return _strip_html(html)
    else:
        try:
            return str(msg.get_content())  # type: ignore[union-attr]
        except Exception:
            payload = msg.get_payload(decode=True)
            if payload:
                return bytes(payload).decode("utf-8", errors="replace")

    return ""


def _get_attachments(msg: EmailMessage | mboxMessage) -> list[dict[str, Any]]:
    """Extract attachments from email."""
    attachments: list[dict[str, Any]] = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        content_disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in content_disposition:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        payload = part.get_payload(decode=True)
        if payload:
            attachments.append({
                "filename": filename,
                "content_type": part.get_content_type(),
                "data": payload,
                "size": len(payload),
            })

    return attachments


def _strip_html(html: str) -> str:
    """Basic HTML to text conversion using stdlib."""
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self._skip = False

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            if tag in ("script", "style"):
                self._skip = True

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style"):
                self._skip = False
            if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
                self.parts.append("\n")

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self.parts.append(data)

    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    # Collapse whitespace
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
