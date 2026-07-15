from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

import pymupdf

SCHEMA_VERSION = "1"
BlockKind = Literal["heading", "paragraph", "list_item", "code"]


@dataclass(frozen=True, slots=True)
class SourceLocation:
    page_number: int | None = None
    title_path: tuple[str, ...] = ()
    url: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "title_path": list(self.title_path),
            "url": self.url,
        }


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    kind: BlockKind
    text: str
    location: SourceLocation

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "text": self.text,
            "location": self.location.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ParsedPage:
    number: int
    blocks: tuple[ParsedBlock, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    media_type: str
    pages: tuple[ParsedPage, ...]
    title: str | None = None
    source_url: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "media_type": self.media_type,
            "title": self.title,
            "source_url": self.source_url,
            "pages": [page.to_dict() for page in self.pages],
        }


def _update_title_path(
    current: tuple[str, ...], level: int, title: str
) -> tuple[str, ...]:
    parent = current[: level - 1]
    return (*parent, title)


def _text_blocks(text: str, *, url: str | None = None) -> tuple[ParsedBlock, ...]:
    blocks: list[ParsedBlock] = []
    title_path: tuple[str, ...] = ()
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        blocks.append(
            ParsedBlock(
                kind="paragraph",
                text="\n".join(paragraph),
                location=SourceLocation(
                    page_number=1,
                    title_path=title_path,
                    url=url,
                ),
            )
        )
        paragraph.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        marker = len(line) - len(line.lstrip("#"))
        if 1 <= marker <= 6 and len(line) > marker and line[marker] == " ":
            flush_paragraph()
            heading = line[marker + 1 :].strip()
            title_path = _update_title_path(title_path, marker, heading)
            blocks.append(
                ParsedBlock(
                    kind="heading",
                    text=heading,
                    location=SourceLocation(
                        page_number=1,
                        title_path=title_path,
                        url=url,
                    ),
                )
            )
            continue
        paragraph.append(line)

    flush_paragraph()
    return tuple(blocks)


def parse_text(
    content: str | bytes,
    *,
    title: str | None = None,
    source_url: str | None = None,
) -> ParsedDocument:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    return ParsedDocument(
        media_type="text/plain",
        title=title,
        source_url=source_url,
        pages=(ParsedPage(number=1, blocks=_text_blocks(text, url=source_url)),),
    )


class _ReadableHtmlParser(HTMLParser):
    _ignored_tags = frozenset({"script", "style", "noscript", "template", "svg"})
    _block_tags = frozenset({"p", "li", "blockquote", "pre"})

    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.blocks: list[ParsedBlock] = []
        self.title: str | None = None
        self.title_path: tuple[str, ...] = ()
        self._ignored_depth = 0
        self._capture_tag: str | None = None
        self._capture_level: int | None = None
        self._text: list[str] = []
        self._in_title = False
        self._title_text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        tag = tag.lower()
        if tag in self._ignored_tags:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = True
            self._title_text.clear()
            return
        if self._capture_tag is not None:
            return
        if tag in self._block_tags:
            self._capture_tag = tag
            self._text.clear()
            return
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            level = int(tag[1])
            if 1 <= level <= 6:
                self._capture_tag = tag
                self._capture_level = level
                self._text.clear()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._ignored_tags:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag == "title" and self._in_title:
            self.title = _normalize_text(self._title_text) or None
            self._in_title = False
            return
        if tag != self._capture_tag:
            return
        text = _normalize_text(self._text)
        if text:
            self._append_block(tag, text)
        self._capture_tag = None
        self._capture_level = None
        self._text.clear()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self._title_text.append(data)
        if self._capture_tag is not None:
            self._text.append(data)

    def _append_block(self, tag: str, text: str) -> None:
        if self._capture_level is not None:
            self.title_path = _update_title_path(
                self.title_path, self._capture_level, text
            )
            kind: BlockKind = "heading"
        elif tag == "li":
            kind = "list_item"
        elif tag == "pre":
            kind = "code"
        else:
            kind = "paragraph"
        self.blocks.append(
            ParsedBlock(
                kind=kind,
                text=text,
                location=SourceLocation(
                    page_number=1,
                    title_path=self.title_path,
                    url=self.source_url,
                ),
            )
        )


def _normalize_text(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def parse_html(
    content: str | bytes,
    *,
    source_url: str,
    title: str | None = None,
) -> ParsedDocument:
    html = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    parser = _ReadableHtmlParser(source_url)
    parser.feed(html)
    parser.close()
    return ParsedDocument(
        media_type="text/html",
        title=title or parser.title,
        source_url=source_url,
        pages=(ParsedPage(number=1, blocks=tuple(parser.blocks)),),
    )


def parse_pdf(
    content: bytes,
    *,
    title: str | None = None,
    source_url: str | None = None,
) -> ParsedDocument:
    pages: list[ParsedPage] = []
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        metadata_title = (pdf.metadata or {}).get("title") or None
        for page_index, page in enumerate(pdf, start=1):
            blocks = tuple(
                ParsedBlock(
                    kind="paragraph",
                    text=text,
                    location=SourceLocation(
                        page_number=page_index,
                        url=source_url,
                    ),
                )
                for raw_block in page.get_text("blocks", sort=True)
                if (text := "\n".join(raw_block[4].splitlines()).strip())
            )
            pages.append(ParsedPage(number=page_index, blocks=blocks))
    return ParsedDocument(
        media_type="application/pdf",
        title=title or metadata_title,
        source_url=source_url,
        pages=tuple(pages),
    )