from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError

from app.infrastructure.execution_tables import outbox_events
from app.infrastructure.source_tables import (
    content_blobs,
    ingestion_runs,
    source_documents,
    source_import_requests,
    source_revisions,
    topic_source_documents,
    topics,
)
from app.infrastructure.web_fetch import FetchedWebPage, WebPageFetcher
from app.parsed_documents import ParsedDocument, parse_html


@dataclass(frozen=True, slots=True)
class SourceImport:
    source_document_id: str
    source_revision_id: str
    ingestion_run_id: str
    title: str
    original_url: str
    final_url: str
    content_hash: str
    fetched_at: datetime
    repeated: bool = False


@dataclass(frozen=True, slots=True)
class SourceImportError:
    code: str


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _markdown(document: ParsedDocument) -> str:
    rendered: list[str] = []
    for page in document.pages:
        for block in page.blocks:
            if block.kind == "heading":
                level = max(1, min(6, len(block.location.title_path)))
                rendered.append(f"{'#' * level} {block.text}")
            elif block.kind == "list_item":
                rendered.append(f"- {block.text}")
            elif block.kind == "code":
                rendered.append(f"```\n{block.text}\n```")
            else:
                rendered.append(block.text)
    return "\n\n".join(rendered).strip() + "\n" if rendered else ""


def _write_content_addressed(root: Path, content_hash: str, content: bytes) -> Path:
    directory = root / content_hash[:2]
    path = directory / f"{content_hash}.md"
    directory.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    temporary = directory / f".{content_hash}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _result(row: Any, *, repeated: bool) -> SourceImport:
    snapshot = dict(row.config_snapshot)
    return SourceImport(
        source_document_id=row.source_document_id,
        source_revision_id=row.source_revision_id,
        ingestion_run_id=row.ingestion_run_id,
        title=snapshot["title"],
        original_url=snapshot["original_url"],
        final_url=snapshot["final_url"],
        content_hash=snapshot["content_hash"],
        fetched_at=datetime.fromisoformat(snapshot["fetched_at"]),
        repeated=repeated,
    )


class WebSourceImportService:
    def __init__(self, engine: Engine, raw_root: Path, fetcher: WebPageFetcher) -> None:
        self.engine = engine
        self.raw_root = raw_root
        self.fetcher = fetcher

    def import_url(
        self,
        *,
        user_id: str,
        topic_id: str,
        url: str,
        request_key: str,
        now: datetime | None = None,
    ) -> SourceImport | SourceImportError:
        normalized_key = request_key.strip()
        normalized_url = url.strip()
        if not normalized_key:
            return SourceImportError("request_key_required")
        request_hash = hashlib.sha256(
            f"{topic_id}\0{normalized_url}".encode("utf-8")
        ).hexdigest()
        existing = self._find_request(user_id=user_id, request_key=normalized_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                return SourceImportError("idempotency_key_conflict")
            return _result(existing, repeated=True)
        with self.engine.connect() as connection:
            owned_topic = connection.execute(
                select(topics.c.id).where(
                    topics.c.id == topic_id,
                    topics.c.user_id == user_id,
                    topics.c.archived_at.is_(None),
                )
            ).scalar_one_or_none()
        if owned_topic is None:
            return SourceImportError("topic_not_found")

        page = self.fetcher.fetch(normalized_url)
        document = parse_html(page.content, source_url=page.final_url)
        markdown = _markdown(document)
        if not markdown.strip():
            return SourceImportError("web_content_empty")
        if len(markdown) > 1_000_000:
            return SourceImportError("web_content_too_large")
        title = (document.title or urlparse_hostname(page.final_url))[:512]
        encoded = markdown.encode("utf-8")
        content_hash = hashlib.sha256(encoded).hexdigest()
        storage_path = _write_content_addressed(self.raw_root, content_hash, encoded)
        return self._persist(
            user_id=user_id,
            topic_id=topic_id,
            request_key=normalized_key,
            request_hash=request_hash,
            page=page,
            title=title,
            content_hash=content_hash,
            storage_path=storage_path,
            byte_size=len(encoded),
            now=now or _utc_now(),
        )

    def _persist(
        self,
        *,
        user_id: str,
        topic_id: str,
        request_key: str,
        request_hash: str,
        page: FetchedWebPage,
        title: str,
        content_hash: str,
        storage_path: Path,
        byte_size: int,
        now: datetime,
    ) -> SourceImport | SourceImportError:
        source_id = str(uuid4())
        revision_id = str(uuid4())
        run_id = str(uuid4())
        snapshot = {
            "input_type": "web_url",
            "title": title,
            "original_url": page.requested_url,
            "final_url": page.final_url,
            "fetched_at": page.fetched_at.isoformat(),
            "content_hash": content_hash,
            "content_type": page.content_type,
        }
        try:
            with self.engine.begin() as connection:
                blob_id = connection.execute(
                    select(content_blobs.c.id).where(
                        content_blobs.c.user_id == user_id,
                        content_blobs.c.content_hash == content_hash,
                    )
                ).scalar_one_or_none()
                if blob_id is None:
                    blob_id = str(uuid4())
                    connection.execute(
                        content_blobs.insert().values(
                            id=blob_id,
                            user_id=user_id,
                            content_hash=content_hash,
                            storage_path=str(storage_path),
                            byte_size=byte_size,
                            created_at=now,
                        )
                    )
                connection.execute(
                    source_documents.insert().values(
                        id=source_id,
                        user_id=user_id,
                        candidate_id=None,
                        input_type="web_url",
                        title=title,
                        state="active",
                        active_revision_id=None,
                        source_missing=False,
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                connection.execute(
                    source_revisions.insert().values(
                        id=revision_id,
                        user_id=user_id,
                        source_document_id=source_id,
                        content_blob_id=blob_id,
                        original_url=page.requested_url,
                        mime_type="text/markdown",
                        page_count=1,
                        content_hash=content_hash,
                        sha256=content_hash,
                        active_ingestion_run_id=None,
                        created_at=now,
                    )
                )
                connection.execute(
                    ingestion_runs.insert().values(
                        id=run_id,
                        user_id=user_id,
                        source_document_id=source_id,
                        source_revision_id=revision_id,
                        request_key=request_key,
                        status="queued",
                        checkpoint="parsing",
                        progress=0,
                        parser_version="parser-v1",
                        chunking_version="parent-child-v1",
                        embedding_index_version="dense-v1",
                        sparse_index_version="bm25-v1",
                        config_snapshot=snapshot,
                        last_error=None,
                        version=1,
                        started_at=None,
                        published_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                connection.execute(
                    topic_source_documents.insert().values(
                        topic_id=topic_id,
                        source_document_id=source_id,
                        created_at=now,
                    )
                )
                connection.execute(
                    source_import_requests.insert().values(
                        id=str(uuid4()),
                        user_id=user_id,
                        request_key=request_key,
                        request_hash=request_hash,
                        source_document_id=source_id,
                        source_revision_id=revision_id,
                        ingestion_run_id=run_id,
                        created_at=now,
                    )
                )
                connection.execute(
                    outbox_events.insert().values(
                        id=str(uuid4()),
                        aggregate_type="ingestion_run",
                        aggregate_id=run_id,
                        event_type="ingestion.run.queued",
                        payload={"run_id": run_id, "checkpoint": "parsing"},
                        status="pending",
                        attempts=0,
                        available_at=now,
                        locked_by=None,
                        locked_until=None,
                        created_at=now,
                        updated_at=now,
                        published_at=None,
                        last_error=None,
                    )
                )
        except IntegrityError:
            existing = self._find_request(user_id=user_id, request_key=request_key)
            if existing is None:
                raise
            if existing.request_hash != request_hash:
                return SourceImportError("idempotency_key_conflict")
            return _result(existing, repeated=True)
        row = self._find_request(user_id=user_id, request_key=request_key)
        if row is None:
            raise RuntimeError("source import transaction did not persist")
        return _result(row, repeated=False)

    def _find_request(self, *, user_id: str, request_key: str) -> Any | None:
        with self.engine.connect() as connection:
            return connection.execute(
                select(
                    source_import_requests.c.source_document_id,
                    source_import_requests.c.source_revision_id,
                    source_import_requests.c.ingestion_run_id,
                    source_import_requests.c.request_hash,
                    ingestion_runs.c.config_snapshot,
                )
                .join(
                    ingestion_runs,
                    ingestion_runs.c.id == source_import_requests.c.ingestion_run_id,
                )
                .where(
                    source_import_requests.c.user_id == user_id,
                    source_import_requests.c.request_key == request_key,
                )
            ).one_or_none()


def urlparse_hostname(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).hostname or "Imported web page"