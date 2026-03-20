"""Tests for email inlet — parsing .eml files and extracting data."""

from __future__ import annotations

from pathlib import Path

import pytest

from lotse.inlets.email import ParsedEmail, _strip_html, parse_eml, save_attachments


@pytest.fixture
def simple_eml(tmp_path: Path) -> Path:
    """Create a simple .eml file for testing."""
    eml_content = (
        "From: sender@example.com\r\n"
        "To: recipient@example.com\r\n"
        "Subject: Telekom Rechnung März 2026\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Sehr geehrter Kunde,\r\n"
        "\r\n"
        "anbei Ihre Rechnung für März 2026.\r\n"
        "Betrag: 39,99 EUR\r\n"
    )
    eml_path = tmp_path / "test.eml"
    eml_path.write_text(eml_content)
    return eml_path


@pytest.fixture
def multipart_eml(tmp_path: Path) -> Path:
    """Create a multipart .eml with attachment."""
    eml_content = (
        "From: sender@example.com\r\n"
        "To: recipient@example.com\r\n"
        "Subject: Document with attachment\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="boundary123"\r\n'
        "\r\n"
        "--boundary123\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Please find attached document.\r\n"
        "\r\n"
        "--boundary123\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        'Content-Disposition: attachment; filename="notes.txt"\r\n'
        "\r\n"
        "These are the attached notes.\r\n"
        "\r\n"
        "--boundary123--\r\n"
    )
    eml_path = tmp_path / "multipart.eml"
    eml_path.write_text(eml_content)
    return eml_path


def test_parse_simple_eml(simple_eml: Path) -> None:
    result = parse_eml(simple_eml)
    assert isinstance(result, ParsedEmail)
    assert "Telekom" in result.subject
    assert "sender@example.com" in result.sender
    assert "39,99 EUR" in result.body
    assert result.attachments == []


def test_parse_multipart_eml(multipart_eml: Path) -> None:
    result = parse_eml(multipart_eml)
    assert "attachment" in result.subject.lower() or "Document" in result.subject
    assert "find attached" in result.body
    assert len(result.attachments) == 1
    assert result.attachments[0]["filename"] == "notes.txt"


def test_text_for_classification(simple_eml: Path) -> None:
    result = parse_eml(simple_eml)
    text = result.text_for_classification
    assert "Subject: Telekom" in text
    assert "From: sender@example.com" in text
    assert "39,99 EUR" in text


def test_save_attachments(multipart_eml: Path, tmp_path: Path) -> None:
    result = parse_eml(multipart_eml)
    dest = tmp_path / "attachments"
    saved = save_attachments(result, dest)

    assert len(saved) == 1
    assert saved[0].exists()
    assert saved[0].name == "notes.txt"
    assert "attached notes" in saved[0].read_text()


def test_save_attachments_handles_collision(multipart_eml: Path, tmp_path: Path) -> None:
    result = parse_eml(multipart_eml)
    dest = tmp_path / "attachments"
    # Save twice — second should get a _1 suffix
    save_attachments(result, dest)
    saved = save_attachments(result, dest)
    assert saved[0].name == "notes_1.txt"


def test_strip_html() -> None:
    html = (
        "<html><body><h1>Title</h1><p>Hello <b>world</b></p><script>evil()</script></body></html>"
    )
    text = _strip_html(html)
    assert "Title" in text
    assert "Hello world" in text
    assert "evil()" not in text


def test_strip_html_empty() -> None:
    assert _strip_html("") == ""


def test_parsed_email_no_attachments() -> None:
    email = ParsedEmail(
        subject="Test",
        sender="a@b.com",
        to="c@d.com",
        body="Hello",
        attachments=[],
    )
    assert email.attachments == []
    assert "Subject: Test" in email.text_for_classification
