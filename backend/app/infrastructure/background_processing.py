from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select, update

from app.chunks import ChunkIdentity, build_chunks
from app.dense_index import DenseIndexService
from app.embedding import EmbeddingConsentSnapshot, EmbeddingRunResult
from app.infrastructure.chunk_store import ChunkStore
from app.infrastructure.embedding_http import HttpsEmbeddingAdapter
from app.infrastructure.embedding_settings import EmbeddingSettingsService
from app.infrastructure.execution import ExecutionRepository, JobClaim
from app.infrastructure.execution_tables import outbox_events
from app.infrastructure.ingestion_runs import IngestionError, IngestionRun, IngestionRunService
from app.infrastructure.parsed_document_store import ParsedDocumentStore
from app.infrastructure.provider_credentials import ProviderCredentialService
from app.infrastructure.source_tables import (
    content_blobs,
    ingestion_runs,
    source_documents,
    source_revisions,
)
from app.infrastructure.sparse_index_store import SparseIndexStore
from app.parsed_documents import ParsedDocument, parse_pdf, parse_text
from app.sparse_index import SparseIndexSnapshot, build_sparse_index

logger = logging.getLogger(__name__)
_ACTIVE_RUN_STATUSES = (
    "queued",
    "running",
    "validating",
    "publishing",
    "cancel_requested",
    "compensating",
)


class _RunChanged(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class IngestionProcessor:
    def __init__(
        self,
        *,
        engine: Engine,
        parsed_store: ParsedDocumentStore,
        chunk_store: ChunkStore,
        sparse_store: SparseIndexStore,
        dense_index: DenseIndexService,
        embedding_settings: EmbeddingSettingsService,
        credentials: ProviderCredentialService,
    ) -> None:
        self._engine = engine
        self._runs = IngestionRunService(engine)
        self._parsed_store = parsed_store
        self._chunk_store = chunk_store
        self._sparse_store = sparse_store
        self._dense_index = dense_index
        self._embedding_settings = embedding_settings
        self._credentials = credentials

    def process_next(self) -> bool:
        run = self._next_run()
        if run is None:
            return False
        try:
            self._process(run)
        except _RunChanged:
            logger.info("ingestion_run_changed", extra={"run_id": run.id})
        except Exception as error:
            logger.exception(
                "ingestion_processing_failed",
                extra={"run_id": run.id, "checkpoint": run.checkpoint},
            )
            self._fail(run.id, error)
        return True

    def _next_run(self) -> IngestionRun | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(ingestion_runs.c.id, ingestion_runs.c.user_id)
                .where(ingestion_runs.c.status.in_(_ACTIVE_RUN_STATUSES))
                .order_by(ingestion_runs.c.created_at, ingestion_runs.c.id)
                .limit(1)
            ).one_or_none()
        if row is None:
            return None
        return self._runs.get(user_id=row.user_id, run_id=row.id)

    def _process(self, initial: IngestionRun) -> None:
        current = initial
        while current.status in _ACTIVE_RUN_STATUSES:
            if current.status == "cancel_requested":
                current = self._require_run(
                    self._runs.begin_compensation(
                        user_id=current.user_id,
                        run_id=current.id,
                        expected_version=current.version,
                    )
                )
                continue
            if current.status == "compensating":
                self._cleanup_run(current)
                current = self._require_run(
                    self._runs.complete_compensation(
                        user_id=current.user_id,
                        run_id=current.id,
                        expected_version=current.version,
                        succeeded=True,
                    )
                )
                continue
            if current.status == "queued":
                current = self._require_run(
                    self._runs.start(
                        user_id=current.user_id,
                        run_id=current.id,
                        expected_version=current.version,
                    )
                )
                continue
            if current.checkpoint == "parsing":
                document = self._parse_source(current)
                self._parsed_store.write(current.id, document)
                current = self._advance(current, "parsing")
                continue
            if current.checkpoint == "extracting":
                current = self._advance(current, "extracting")
                continue
            if current.checkpoint == "chunking":
                document = self._parse_source(current)
                dense_version = self._dense_version(current)
                self._set_dense_version(current.id, dense_version)
                chunks = build_chunks(
                    document,
                    identity=ChunkIdentity(
                        user_id=current.user_id,
                        source_document_id=current.source_document_id,
                        source_revision_id=current.source_revision_id,
                        ingestion_run_id=current.id,
                    ),
                    chunking_version=current.chunking_version,
                    dense_index_version=dense_version,
                    sparse_index_version=current.sparse_index_version,
                )
                if not chunks.children:
                    raise ValueError("ingestion produced no searchable chunks")
                self._chunk_store.write(chunks)
                current = self._advance(current, "chunking")
                continue
            if current.checkpoint == "embedding":
                chunks = self._chunk_store.read(current.id)
                self._write_sparse(current.sparse_index_version, current.id, chunks.children)
                if current.embedding_index_version:
                    self._write_dense(current, chunks.children)
                current = self._advance(current, "embedding")
                continue
            if current.checkpoint == "validating":
                chunks = self._chunk_store.read(current.id)
                sparse = self._sparse_store.read(current.sparse_index_version)
                if not chunks.children or not any(
                    item.ingestion_run_id == current.id for item in sparse.documents
                ):
                    raise ValueError("ingestion validation failed")
                current = self._advance(current, "validating")
                continue
            if current.checkpoint == "publishing":
                current = self._require_run(
                    self._runs.publish(
                        user_id=current.user_id,
                        run_id=current.id,
                        expected_version=current.version,
                    )
                )
                self._mark_events_published(current.id)
                logger.info("ingestion_run_published", extra={"run_id": current.id})
                return
            raise ValueError(f"unsupported ingestion checkpoint: {current.checkpoint}")

    def _parse_source(self, run: IngestionRun) -> ParsedDocument:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(
                    content_blobs.c.storage_path,
                    source_revisions.c.mime_type,
                    source_revisions.c.original_url,
                    source_documents.c.title,
                )
                .select_from(
                    source_revisions.join(
                        content_blobs,
                        source_revisions.c.content_blob_id == content_blobs.c.id,
                    ).join(
                        source_documents,
                        source_documents.c.id == source_revisions.c.source_document_id,
                    )
                )
                .where(
                    source_revisions.c.id == run.source_revision_id,
                    source_revisions.c.user_id == run.user_id,
                )
            ).one()
        content = Path(row.storage_path).read_bytes()
        if row.mime_type == "application/pdf":
            return parse_pdf(content, title=row.title, source_url=row.original_url)
        return parse_text(content, title=row.title, source_url=row.original_url)

    def _dense_version(self, run: IngestionRun) -> str:
        settings = self._embedding_settings.get(user_id=run.user_id)
        if settings is None or not settings.enabled:
            return ""
        return settings.spec.index_version(run.chunking_version)

    def _set_dense_version(self, run_id: str, version: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(ingestion_runs)
                .where(ingestion_runs.c.id == run_id)
                .values(embedding_index_version=version)
            )

    def _write_sparse(self, index_version: str, run_id: str, children: tuple[Any, ...]) -> None:
        current = build_sparse_index(children, index_version=index_version)
        try:
            existing = self._sparse_store.read(index_version)
        except FileNotFoundError:
            existing = SparseIndexSnapshot(index_version=index_version, documents=())
        documents = {
            item.child_chunk_id: item
            for item in existing.documents
            if item.ingestion_run_id != run_id
        }
        documents.update({item.child_chunk_id: item for item in current.documents})
        self._sparse_store.write(
            SparseIndexSnapshot(
                index_version=index_version,
                documents=tuple(documents[key] for key in sorted(documents)),
            )
        )

    def _write_dense(self, run: IngestionRun, children: tuple[Any, ...]) -> None:
        settings = self._embedding_settings.get(user_id=run.user_id)
        if settings is None or not settings.enabled:
            raise ValueError("dense version configured without embedding authorization")
        adapter = HttpsEmbeddingAdapter(
            settings.spec,
            secret_resolver=lambda user_id, credential_name: self._credentials.reveal(
                user_id=user_id,
                provider=credential_name,
            ),
        )
        consent = EmbeddingConsentSnapshot(
            user_id=run.user_id,
            ingestion_run_id=run.id,
            provider=settings.spec.provider,
            provider_config_version=settings.version,
            authorization_source="user_embedding_settings",
            authorization_version=settings.version,
            allowed_data_categories=frozenset({"source_chunk_text"}),
            external_processing_allowed=True,
        )
        vectors = adapter.embed(children, consent=consent)
        self._dense_index.rebuild_run(
            children,
            EmbeddingRunResult(
                provider=settings.spec.provider,
                model_identifier=settings.spec.model_identifier,
                index_version=settings.spec.index_version(run.chunking_version),
                vectors=vectors,
                used_fallback=False,
            ),
        )

    def _advance(self, run: IngestionRun, checkpoint: str) -> IngestionRun:
        return self._require_run(
            self._runs.advance(
                user_id=run.user_id,
                run_id=run.id,
                expected_version=run.version,
                completed_checkpoint=checkpoint,
            )
        )

    def _fail(self, run_id: str, error: Exception) -> None:
        with self._engine.connect() as connection:
            identity = connection.execute(
                select(ingestion_runs.c.user_id).where(ingestion_runs.c.id == run_id)
            ).scalar_one_or_none()
        if identity is None:
            return
        current = self._runs.get(user_id=identity, run_id=run_id)
        if current is None or current.status not in {"queued", "running", "validating", "publishing"}:
            return
        failed = self._runs.fail(
            user_id=current.user_id,
            run_id=current.id,
            expected_version=current.version,
            error={"code": type(error).__name__, "message": str(error), "retryable": False},
        )
        if isinstance(failed, IngestionError):
            return
        self._cleanup_run(failed)
        self._runs.complete_compensation(
            user_id=failed.user_id,
            run_id=failed.id,
            expected_version=failed.version,
            succeeded=True,
        )

    def _cleanup_run(self, run: IngestionRun) -> None:
        shutil.rmtree(self._chunk_store.artifact_path(run.id).parent, ignore_errors=True)
        if run.embedding_index_version:
            self._dense_index.delete_runs(run.embedding_index_version, frozenset({run.id}))
        try:
            snapshot = self._sparse_store.read(run.sparse_index_version)
        except FileNotFoundError:
            return
        self._sparse_store.write(
            SparseIndexSnapshot(
                index_version=snapshot.index_version,
                documents=tuple(
                    item for item in snapshot.documents if item.ingestion_run_id != run.id
                ),
            )
        )

    def _mark_events_published(self, run_id: str) -> None:
        now = _utc_now()
        with self._engine.begin() as connection:
            connection.execute(
                update(outbox_events)
                .where(
                    outbox_events.c.aggregate_type == "ingestion_run",
                    outbox_events.c.aggregate_id == run_id,
                    outbox_events.c.status.in_(("pending", "publishing")),
                )
                .values(
                    status="published",
                    locked_by=None,
                    locked_until=None,
                    published_at=now,
                    updated_at=now,
                )
            )

    @staticmethod
    def _require_run(result: IngestionRun | IngestionError) -> IngestionRun:
        if isinstance(result, IngestionError):
            if result.code in {
                "ingestion_version_conflict",
                "ingestion_transition_invalid",
            }:
                raise _RunChanged(result.code)
            raise RuntimeError(result.code)
        return result


class SourcePurgeProcessor:
    def __init__(
        self,
        *,
        engine: Engine,
        parsed_root: Path,
        sparse_store: SparseIndexStore,
        dense_index: DenseIndexService,
        worker_id: str,
    ) -> None:
        self._engine = engine
        self._parsed_root = parsed_root
        self._sparse_store = sparse_store
        self._dense_index = dense_index
        self._execution = ExecutionRepository(engine)
        self._worker_id = worker_id

    def process_next(self) -> bool:
        claim = self._execution.claim_next_job(
            worker_id=self._worker_id,
            lease_seconds=120,
        )
        if claim is None:
            return False
        try:
            if claim.job_type in {"source.purge", "source.purge_due"}:
                self._purge(claim)
            self._execution.complete_job(
                job_id=claim.job_id,
                worker_id=self._worker_id,
            )
        except Exception as error:
            logger.exception(
                "background_job_failed",
                extra={"job_id": claim.job_id, "job_type": claim.job_type},
            )
            self._execution.fail_job(
                job_id=claim.job_id,
                worker_id=self._worker_id,
                error={"code": type(error).__name__, "message": str(error)},
            )
        return True

    def _purge(self, claim: JobClaim) -> None:
        user_id = str(claim.payload["user_id"])
        source_id = str(claim.payload["source_document_id"])
        expected_state = "trashed" if claim.job_type == "source.purge_due" else "purging"
        with self._engine.begin() as connection:
            source = connection.execute(
                select(source_documents)
                .where(
                    source_documents.c.id == source_id,
                    source_documents.c.user_id == user_id,
                )
                .with_for_update()
            ).one_or_none()
            if source is None or source.state == "purged":
                return
            if source.state != expected_state:
                return
            runs = connection.execute(
                select(
                    ingestion_runs.c.id,
                    ingestion_runs.c.embedding_index_version,
                    ingestion_runs.c.sparse_index_version,
                ).where(
                    ingestion_runs.c.source_document_id == source_id,
                    ingestion_runs.c.user_id == user_id,
                )
            ).all()
            run_ids = frozenset(row.id for row in runs)
            for row in runs:
                shutil.rmtree(self._parsed_root / row.id, ignore_errors=True)
            for version in {
                row.embedding_index_version for row in runs if row.embedding_index_version
            }:
                self._dense_index.delete_runs(version, run_ids)
            for version in {row.sparse_index_version for row in runs}:
                try:
                    snapshot = self._sparse_store.read(version)
                except FileNotFoundError:
                    continue
                self._sparse_store.write(
                    SparseIndexSnapshot(
                        index_version=version,
                        documents=tuple(
                            item
                            for item in snapshot.documents
                            if item.ingestion_run_id not in run_ids
                        ),
                    )
                )
            now = _utc_now()
            connection.execute(
                update(source_documents)
                .where(
                    source_documents.c.id == source_id,
                    source_documents.c.user_id == user_id,
                    source_documents.c.state == expected_state,
                )
                .values(
                    state="purged",
                    active_revision_id=None,
                    source_missing=True,
                    purged_at=now,
                    updated_at=now,
                    version=source_documents.c.version + 1,
                )
            )