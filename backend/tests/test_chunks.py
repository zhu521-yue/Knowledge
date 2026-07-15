import json
from pathlib import Path

import pytest

from app.chunks import (
    ChunkIdentity,
    ChunkingConfig,
    ChunkLimitExceeded,
    build_chunks,
)
from app.infrastructure.chunk_store import ChunkStore
from app.parsed_documents import ParsedBlock, ParsedDocument, ParsedPage, SourceLocation


def _identity(run_id: str = "run-1") -> ChunkIdentity:
    return ChunkIdentity(
        user_id="user-1",
        source_document_id="source-1",
        source_revision_id="revision-1",
        ingestion_run_id=run_id,
    )


def _document() -> ParsedDocument:
    return ParsedDocument(
        media_type="application/pdf",
        pages=(
            ParsedPage(
                number=1,
                blocks=(
                    ParsedBlock(
                        kind="paragraph",
                        text="one two three four five six",
                        location=SourceLocation(
                            page_number=1, title_path=("First",)
                        ),
                    ),
                ),
            ),
            ParsedPage(
                number=2,
                blocks=(
                    ParsedBlock(
                        kind="paragraph",
                        text="seven eight nine ten",
                        location=SourceLocation(
                            page_number=2, title_path=("Second",)
                        ),
                    ),
                ),
            ),
        ),
    )


def test_builds_traceable_parent_and_overlapping_child_chunks() -> None:
    chunks = build_chunks(
        _document(),
        identity=_identity(),
        chunking_version="parent-child-v1",
        dense_index_version="dense-v1",
        sparse_index_version="bm25-v1",
        config=ChunkingConfig(
            parent_max_tokens=10,
            child_max_tokens=4,
            child_overlap_tokens=1,
        ),
    )

    assert len(chunks.parents) == 2
    assert [parent.heading_path for parent in chunks.parents] == [
        ("First",),
        ("Second",),
    ]
    assert [parent.page_start for parent in chunks.parents] == [1, 2]
    assert all(child.parent_chunk_id in {p.id for p in chunks.parents} for child in chunks.children)
    assert [child.text for child in chunks.children[:2]] == [
        "one two three four",
        "four five six",
    ]
    assert chunks.children[0].parent_char_start == 0
    assert chunks.children[1].parent_char_start < chunks.children[0].parent_char_end
    assert chunks.children[0].heading_path == ("First",)
    assert chunks.children[0].page_start == chunks.children[0].page_end == 1


def test_ids_are_deterministic_and_scoped_to_run_and_chunking_version() -> None:
    arguments = {
        "document": _document(),
        "identity": _identity(),
        "chunking_version": "parent-child-v1",
        "dense_index_version": "dense-v1",
        "sparse_index_version": "bm25-v1",
    }

    first = build_chunks(**arguments)
    retry = build_chunks(**arguments)
    new_run = build_chunks(**{**arguments, "identity": _identity("run-2")})
    new_version = build_chunks(
        **{**arguments, "chunking_version": "parent-child-v2"}
    )

    assert [parent.id for parent in first.parents] == [
        parent.id for parent in retry.parents
    ]
    assert [child.id for child in first.children] == [
        child.id for child in retry.children
    ]
    assert first.parents[0].id != new_run.parents[0].id
    assert first.parents[0].id != new_version.parents[0].id
    assert first.children[0].id != new_version.children[0].id


def test_oversized_structured_block_is_split_without_exceeding_parent_limit() -> None:
    document = ParsedDocument(
        media_type="text/plain",
        pages=(
            ParsedPage(
                number=1,
                blocks=(
                    ParsedBlock(
                        kind="code",
                        text="一二三四五六七八九十",
                        location=SourceLocation(
                            page_number=1, title_path=("代码",)
                        ),
                    ),
                ),
            ),
        ),
    )

    chunks = build_chunks(
        document,
        identity=_identity(),
        chunking_version="parent-child-v1",
        dense_index_version="dense-v1",
        sparse_index_version="bm25-v1",
        config=ChunkingConfig(
            parent_max_tokens=4,
            child_max_tokens=3,
            child_overlap_tokens=0,
        ),
    )

    assert [parent.token_count for parent in chunks.parents] == [4, 4, 2]
    assert "".join(parent.text for parent in chunks.parents) == "一二三四五六七八九十"


def test_rejects_child_count_over_run_budget() -> None:
    with pytest.raises(ChunkLimitExceeded):
        build_chunks(
            _document(),
            identity=_identity(),
            chunking_version="parent-child-v1",
            dense_index_version="dense-v1",
            sparse_index_version="bm25-v1",
            config=ChunkingConfig(
                parent_max_tokens=10,
                child_max_tokens=2,
                child_overlap_tokens=0,
                max_children=2,
            ),
        )


def test_store_isolates_immutable_run_artifacts(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path)
    common = {
        "document": _document(),
        "chunking_version": "parent-child-v1",
        "dense_index_version": "dense-v1",
        "sparse_index_version": "bm25-v1",
    }
    first = build_chunks(**common, identity=_identity("run-1"))
    second = build_chunks(**common, identity=_identity("run-2"))

    first_path = store.write(first)
    second_path = store.write(second)

    assert first_path == tmp_path / "run-1" / "chunks.v1.json"
    assert second_path == tmp_path / "run-2" / "chunks.v1.json"
    assert json.loads(first_path.read_text(encoding="utf-8"))["ingestion_run_id"] == "run-1"
    assert json.loads(second_path.read_text(encoding="utf-8"))["ingestion_run_id"] == "run-2"
    assert list(tmp_path.rglob("*.tmp")) == []