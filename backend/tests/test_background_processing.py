from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select

from app.dense_index import DenseIndexRecord, DenseIndexService, DenseSearchHit
from app.infrastructure.background_processing import IngestionProcessor, SourcePurgeProcessor
from app.infrastructure.chunk_store import ChunkStore
from app.infrastructure.embedding_settings import EmbeddingSettingsService
from app.infrastructure.execution_tables import execution_metadata, worker_jobs
from app.infrastructure.identity import IdentityService
from app.infrastructure.identity_tables import identity_metadata
from app.infrastructure.parsed_document_store import ParsedDocumentStore
from app.infrastructure.provider_credentials import ProviderCredentialService
from app.infrastructure.retrieval_artifacts import FileRetrievalArtifacts
from app.infrastructure.retrieval_scope import RetrievalScopeResolver
from app.infrastructure.source_imports import SourceImport, WebSourceImportService
from app.infrastructure.source_lifecycle import SourceLifecycleService
from app.infrastructure.source_tables import ingestion_runs, source_documents
from app.infrastructure.sparse_index_store import SparseIndexStore
from app.infrastructure.topics import TopicService
from app.infrastructure.web_fetch import FetchedWebPage
from app.retrieval_scope import AuthorizedRetrievalScope
from app.retrieval_service import RetrieveTopicParents

_MASTER_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


class _Fetcher:
    def fetch(self, url: str) -> FetchedWebPage:
        return FetchedWebPage(
            requested_url=url,
            final_url="https://www.example.test/guide",
            content=(
                b"<html><head><title>Guide</title></head>"
                b"<body><h1>Intro</h1><p>Hello world.</p></body></html>"
            ),
            content_type="text/html",
            fetched_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        )


class _DenseBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def ensure_collection(self, index_version: str, dimension: int) -> None:
        self.calls.append("ensure")

    def upsert(
        self, index_version: str, records: Sequence[DenseIndexRecord]
    ) -> None:
        self.calls.append("upsert")

    def delete_runs(self, index_version: str, run_ids: frozenset[str]) -> None:
        self.calls.append("delete")

    def search(
        self,
        index_version: str,
        query_vector: Sequence[float],
        *,
        scope: AuthorizedRetrievalScope,
        limit: int,
    ) -> Sequence[DenseSearchHit]:
        self.calls.append("search")
        return ()


class _ForbiddenQueryEmbedding:
    def embed_query(
        self,
        query: str,
        *,
        user_id: str,
        topic_id: str,
        index_version: str,
        chunking_version: str,
    ) -> Sequence[float]:
        raise AssertionError("BM25-only retrieval must not call an embedding provider")


def _fixture(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'worker.db'}")
    execution_metadata.create_all(engine)
    identity_metadata.create_all(engine)
    user = IdentityService(engine).bootstrap_admin(
        email="worker@example.test",
        password="correct horse battery staple",
        display_name="Worker",
    )
    topic = TopicService(engine).create(user_id=user.id, name="Worker")
    imported = WebSourceImportService(
        engine, tmp_path / "raw", _Fetcher()
    ).import_url(
        user_id=user.id,
        topic_id=topic.id,
        url="https://www.example.test/start",
        request_key="worker-import",
    )
    assert isinstance(imported, SourceImport)
    dense_backend = _DenseBackend()
    dense = DenseIndexService(dense_backend)
    sparse = SparseIndexStore(tmp_path / "cache" / "sparse")
    chunks = ChunkStore(tmp_path / "parsed")
    processor = IngestionProcessor(
        engine=engine,
        parsed_store=ParsedDocumentStore(tmp_path / "parsed"),
        chunk_store=chunks,
        sparse_store=sparse,
        dense_index=dense,
        embedding_settings=EmbeddingSettingsService(engine),
        credentials=ProviderCredentialService(engine, _MASTER_KEY),
    )
    return engine, user.id, topic.id, imported, processor, dense, dense_backend, sparse, chunks


def test_worker_publishes_bm25_only_import_and_retrieval_returns_evidence(
    tmp_path: Path,
) -> None:
    (
        engine,
        user_id,
        topic_id,
        imported,
        processor,
        dense,
        dense_backend,
        sparse,
        chunks,
    ) = _fixture(tmp_path)

    assert processor.process_next() is True
    assert processor.process_next() is False

    with engine.connect() as connection:
        run = connection.execute(
            select(ingestion_runs).where(ingestion_runs.c.id == imported.ingestion_run_id)
        ).one()
        source = connection.execute(
            select(source_documents).where(
                source_documents.c.id == imported.source_document_id
            )
        ).one()
    assert run.status == "published"
    assert run.embedding_index_version == ""
    assert source.active_revision_id == imported.source_revision_id
    assert chunks.artifact_path(run.id).exists()
    assert any(
        document.ingestion_run_id == run.id
        for document in sparse.read(run.sparse_index_version).documents
    )
    assert dense_backend.calls == []

    result = RetrieveTopicParents(
        scope_resolver=RetrievalScopeResolver(engine),
        query_embedding=_ForbiddenQueryEmbedding(),
        dense_index=dense,
        artifacts=FileRetrievalArtifacts(chunks=chunks, sparse_indexes=sparse),
    ).execute(user_id=user_id, topic_id=topic_id, query="Hello")

    assert result.dense_index_version == ""
    assert result.active_run_ids == (run.id,)
    assert result.parents
    assert "Hello world" in result.parents[0].text
    assert result.parents[0].evidence[0].dense_rank is None
    assert dense_backend.calls == []


def test_worker_records_failure_when_source_artifact_is_missing(tmp_path: Path) -> None:
    (
        engine,
        _user_id,
        _topic_id,
        imported,
        processor,
        _dense,
        _dense_backend,
        _sparse,
        _chunks,
    ) = _fixture(tmp_path)
    for path in (tmp_path / "raw").rglob("*.md"):
        path.unlink()

    assert processor.process_next() is True

    with engine.connect() as connection:
        run = connection.execute(
            select(ingestion_runs).where(ingestion_runs.c.id == imported.ingestion_run_id)
        ).one()
    assert run.status == "failed"
    assert run.last_error["code"] == "FileNotFoundError"
    assert run.last_error["retryable"] is False


def test_purge_job_removes_artifacts_and_finishes_source(tmp_path: Path) -> None:
    (
        engine,
        user_id,
        _topic_id,
        imported,
        processor,
        dense,
        _dense_backend,
        sparse,
        _chunks,
    ) = _fixture(tmp_path)
    processor.process_next()
    lifecycle = SourceLifecycleService(engine)
    trashed = lifecycle.command(
        user_id=user_id,
        source_id=imported.source_document_id,
        command="trash",
        expected_version=2,
        request_key="trash",
    )
    purging = lifecycle.command(
        user_id=user_id,
        source_id=imported.source_document_id,
        command="purge",
        expected_version=trashed.version,
        request_key="purge",
    )
    assert purging.state == "purging"

    purges = SourcePurgeProcessor(
        engine=engine,
        parsed_root=tmp_path / "parsed",
        sparse_store=sparse,
        dense_index=dense,
        worker_id="test-worker",
    )
    assert purges.process_next() is True

    source = lifecycle.get(user_id=user_id, source_id=imported.source_document_id)
    assert source is not None
    assert source.state == "purged"
    assert not (tmp_path / "parsed" / imported.ingestion_run_id).exists()
    assert all(
        document.ingestion_run_id != imported.ingestion_run_id
        for document in sparse.read("bm25-v1").documents
    )
    with engine.connect() as connection:
        immediate_job = connection.execute(
            select(worker_jobs).where(worker_jobs.c.job_type == "source.purge")
        ).one()
    assert immediate_job.status == "succeeded"