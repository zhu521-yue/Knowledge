import json
import os
from pathlib import Path

import pymupdf
import pytest

from app.infrastructure.parsed_document_store import (
    InvalidRunId,
    ParsedDocumentStore,
)
from app.parsed_documents import parse_html, parse_pdf, parse_text


def test_text_parser_preserves_heading_path() -> None:
    document = parse_text(
        "# Overview\n\nFirst paragraph.\n\n## Details\nSecond paragraph.",
        title="Notes",
    )

    assert document.to_dict() == {
        "schema_version": "1",
        "media_type": "text/plain",
        "title": "Notes",
        "source_url": None,
        "pages": [
            {
                "number": 1,
                "blocks": [
                    {
                        "kind": "heading",
                        "text": "Overview",
                        "location": {
                            "page_number": 1,
                            "title_path": ["Overview"],
                            "url": None,
                        },
                    },
                    {
                        "kind": "paragraph",
                        "text": "First paragraph.",
                        "location": {
                            "page_number": 1,
                            "title_path": ["Overview"],
                            "url": None,
                        },
                    },
                    {
                        "kind": "heading",
                        "text": "Details",
                        "location": {
                            "page_number": 1,
                            "title_path": ["Overview", "Details"],
                            "url": None,
                        },
                    },
                    {
                        "kind": "paragraph",
                        "text": "Second paragraph.",
                        "location": {
                            "page_number": 1,
                            "title_path": ["Overview", "Details"],
                            "url": None,
                        },
                    },
                ],
            }
        ],
    }


def test_html_parser_extracts_readable_blocks_and_url_locations() -> None:
    document = parse_html(
        """
        <html><head><title>Example</title><style>hidden</style></head>
        <body><h1>Guide</h1><p>Hello <strong>world</strong>.</p>
        <script>ignored()</script><h2>Steps</h2><ul><li>First</li></ul></body></html>
        """,
        source_url="https://example.test/guide",
    )

    assert document.title == "Example"
    assert [block.text for block in document.pages[0].blocks] == [
        "Guide",
        "Hello world .",
        "Steps",
        "First",
    ]
    assert [block.location.title_path for block in document.pages[0].blocks] == [
        ("Guide",),
        ("Guide",),
        ("Guide", "Steps"),
        ("Guide", "Steps"),
    ]
    assert all(
        block.location.url == "https://example.test/guide"
        for block in document.pages[0].blocks
    )
    assert "ignored" not in json.dumps(document.to_dict())
    assert "hidden" not in json.dumps(document.to_dict())


def test_pdf_parser_uses_real_pages_and_preserves_page_numbers() -> None:
    pdf = pymupdf.open()
    first_page = pdf.new_page()
    first_page.insert_text((72, 72), "First page")
    second_page = pdf.new_page()
    second_page.insert_text((72, 72), "Second page")
    content = pdf.tobytes()
    pdf.close()

    document = parse_pdf(content, title="Sample")

    assert document.media_type == "application/pdf"
    assert document.title == "Sample"
    assert [page.number for page in document.pages] == [1, 2]
    assert [page.blocks[0].text for page in document.pages] == [
        "First page",
        "Second page",
    ]
    assert [page.blocks[0].location.page_number for page in document.pages] == [1, 2]


def test_store_atomically_overwrites_versioned_json(tmp_path: Path) -> None:
    store = ParsedDocumentStore(tmp_path)
    first = parse_text("First")
    second = parse_text("Second")

    path = store.write("run-1", first)
    store.write("run-1", second)

    assert path == tmp_path / "run-1" / "parsed-document.v1.json"
    assert json.loads(path.read_text(encoding="utf-8")) == second.to_dict()
    assert list(path.parent.glob("*.tmp")) == []
    assert list(path.parent.glob(".*.tmp")) == []


def test_store_keeps_previous_artifact_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ParsedDocumentStore(tmp_path)
    path = store.write("run-1", parse_text("Published"))

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.write("run-1", parse_text("Incomplete"))

    assert json.loads(path.read_text(encoding="utf-8")) == parse_text(
        "Published"
    ).to_dict()
    assert list(path.parent.glob(".*.tmp")) == []


@pytest.mark.parametrize("run_id", ["../escape", "nested/run", "", ".hidden"])
def test_store_rejects_unsafe_run_ids(tmp_path: Path, run_id: str) -> None:
    store = ParsedDocumentStore(tmp_path)

    with pytest.raises(InvalidRunId):
        store.write(run_id, parse_text("content"))