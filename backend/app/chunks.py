from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterator

from app.parsed_documents import ParsedBlock, ParsedDocument

CHUNK_SCHEMA_VERSION = "1"
_TOKEN_PATTERN = re.compile(
    r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\W\d_]+|[^\w\s]", re.UNICODE
)


@dataclass(frozen=True, slots=True)
class ChunkIdentity:
    user_id: str
    source_document_id: str
    source_revision_id: str
    ingestion_run_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "user_id": self.user_id,
            "source_document_id": self.source_document_id,
            "source_revision_id": self.source_revision_id,
            "ingestion_run_id": self.ingestion_run_id,
        }


@dataclass(frozen=True, slots=True)
class ParentChunk:
    id: str
    ordinal: int
    identity: ChunkIdentity
    heading_path: tuple[str, ...]
    page_start: int
    page_end: int
    document_char_start: int
    document_char_end: int
    token_count: int
    text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "ordinal": self.ordinal,
            **self.identity.to_dict(),
            "heading_path": list(self.heading_path),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "document_char_start": self.document_char_start,
            "document_char_end": self.document_char_end,
            "token_count": self.token_count,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class ChildChunk:
    id: str
    parent_chunk_id: str
    ordinal: int
    identity: ChunkIdentity
    parent_char_start: int
    parent_char_end: int
    heading_path: tuple[str, ...]
    page_start: int
    page_end: int
    token_count: int
    dense_index_version: str
    sparse_index_version: str
    text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "parent_chunk_id": self.parent_chunk_id,
            "ordinal": self.ordinal,
            **self.identity.to_dict(),
            "parent_char_start": self.parent_char_start,
            "parent_char_end": self.parent_char_end,
            "heading_path": list(self.heading_path),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "token_count": self.token_count,
            "dense_index_version": self.dense_index_version,
            "sparse_index_version": self.sparse_index_version,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class ChunkSet:
    identity: ChunkIdentity
    chunking_version: str
    parents: tuple[ParentChunk, ...]
    children: tuple[ChildChunk, ...]
    schema_version: str = CHUNK_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "chunking_version": self.chunking_version,
            **self.identity.to_dict(),
            "parents": [parent.to_dict() for parent in self.parents],
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    parent_max_tokens: int = 2_500
    child_max_tokens: int = 600
    child_overlap_tokens: int = 80
    max_children: int = 1_500

    def __post_init__(self) -> None:
        if self.parent_max_tokens < 1 or self.child_max_tokens < 1:
            raise ValueError("chunk token limits must be positive")
        if not 0 <= self.child_overlap_tokens < self.child_max_tokens:
            raise ValueError("child overlap must be smaller than child limit")
        if self.max_children < 1:
            raise ValueError("max_children must be positive")


@dataclass(frozen=True, slots=True)
class _SourceUnit:
    block: ParsedBlock
    separator_before: str
    document_start: int
    document_end: int


class ChunkLimitExceeded(ValueError):
    pass


def _stable_id(*parts: object) -> str:
    value = "\x1f".join(str(part) for part in parts).encode()
    return hashlib.sha256(value).hexdigest()


def _token_spans(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in _TOKEN_PATTERN.finditer(text)]


def _windows(text: str, maximum: int, overlap: int = 0) -> Iterator[tuple[int, int]]:
    spans = _token_spans(text)
    if not spans:
        return
    step = maximum - overlap
    for token_start in range(0, len(spans), step):
        token_end = min(token_start + maximum, len(spans))
        yield spans[token_start][0], spans[token_end - 1][1]
        if token_end == len(spans):
            break


def _source_units(
    document: ParsedDocument, maximum: int
) -> tuple[_SourceUnit, ...]:
    units: list[_SourceUnit] = []
    cursor = 0
    for page in document.pages:
        for block in page.blocks:
            separator = "\n\n" if units else ""
            cursor += len(separator)
            windows = tuple(_windows(block.text, maximum))
            for part_index, (start, end) in enumerate(windows):
                text = block.text[start:end]
                units.append(
                    _SourceUnit(
                        block=ParsedBlock(block.kind, text, block.location),
                        separator_before=separator if part_index == 0 else "",
                        document_start=cursor + start,
                        document_end=cursor + end,
                    )
                )
            cursor += len(block.text)
    return tuple(units)


def _parent_groups(
    units: tuple[_SourceUnit, ...], maximum: int
) -> Iterator[tuple[_SourceUnit, ...]]:
    group: list[_SourceUnit] = []
    tokens = 0
    heading_path: tuple[str, ...] | None = None
    for unit in units:
        unit_tokens = len(_token_spans(unit.block.text))
        boundary = (
            group
            and (
                heading_path != unit.block.location.title_path
                or tokens + unit_tokens > maximum
            )
        )
        if boundary:
            yield tuple(group)
            group = []
            tokens = 0
        group.append(unit)
        tokens += unit_tokens
        heading_path = unit.block.location.title_path
    if group:
        yield tuple(group)


def build_chunks(
    document: ParsedDocument,
    *,
    identity: ChunkIdentity,
    chunking_version: str,
    dense_index_version: str,
    sparse_index_version: str,
    config: ChunkingConfig = ChunkingConfig(),
) -> ChunkSet:
    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []
    groups = _parent_groups(
        _source_units(document, config.parent_max_tokens), config.parent_max_tokens
    )
    for parent_ordinal, group in enumerate(groups):
        text_parts: list[str] = []
        unit_ranges: list[tuple[int, int, _SourceUnit]] = []
        text_cursor = 0
        for index, unit in enumerate(group):
            separator = unit.separator_before if index else ""
            text_parts.append(separator)
            text_cursor += len(separator)
            start = text_cursor
            text_parts.append(unit.block.text)
            text_cursor += len(unit.block.text)
            unit_ranges.append((start, text_cursor, unit))
        text = "".join(text_parts)
        page_numbers = [unit.block.location.page_number or 1 for unit in group]
        parent_id = _stable_id(
            identity.ingestion_run_id, parent_ordinal, chunking_version
        )
        parent = ParentChunk(
            id=parent_id,
            ordinal=parent_ordinal,
            identity=identity,
            heading_path=group[0].block.location.title_path,
            page_start=min(page_numbers),
            page_end=max(page_numbers),
            document_char_start=group[0].document_start,
            document_char_end=group[-1].document_end,
            token_count=len(_token_spans(text)),
            text=text,
        )
        parents.append(parent)
        for child_ordinal, (start, end) in enumerate(
            _windows(text, config.child_max_tokens, config.child_overlap_tokens)
        ):
            child_text = text[start:end]
            child_pages = [
                unit.block.location.page_number or 1
                for unit_start, unit_end, unit in unit_ranges
                if unit_start < end and unit_end > start
            ]
            children.append(
                ChildChunk(
                    id=_stable_id(parent_id, child_ordinal, chunking_version),
                    parent_chunk_id=parent_id,
                    ordinal=child_ordinal,
                    identity=identity,
                    parent_char_start=start,
                    parent_char_end=end,
                    heading_path=parent.heading_path,
                    page_start=min(child_pages),
                    page_end=max(child_pages),
                    token_count=len(_token_spans(child_text)),
                    dense_index_version=dense_index_version,
                    sparse_index_version=sparse_index_version,
                    text=child_text,
                )
            )
            if len(children) > config.max_children:
                raise ChunkLimitExceeded("child chunk limit exceeded")
    return ChunkSet(
        identity=identity,
        chunking_version=chunking_version,
        parents=tuple(parents),
        children=tuple(children),
    )