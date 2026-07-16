from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

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
from app.parsed_documents import ParsedDocument, parse_html, parse_pdf


@dataclass(frozen=True, slots=True)
class SourceImport:
    source_document_id: str
    source_revision_id: str
    ingestion_run_id: str
    input_type: str
    title: str
    content_hash: str
    original_url: str | None = None
    final_url: str | None = None
    fetched_at: datetime | None = None
    repeated: bool = False


@dataclass(frozen=True, slots=True)
class SourceImportError:
    code: str


def _normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def _source_candidate_id(topic_id: str, url: str) -> str:
    identity = f"knowledge:web-source:{topic_id}:{_normalize_url(url)}"
    return str(uuid5(NAMESPACE_URL, identity))


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


def _write_content_addressed(
    root: Path,
    content_hash: str,
    content: bytes,
    *,
    suffix: str = ".md",
) -> Path:
    directory = root / content_hash[:2]
    path = directory / f"{content_hash}{suffix}"
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
        input_type=snapshot["input_type"],
        title=snapshot["title"],
        original_url=snapshot.get("original_url"),
        final_url=snapshot.get("final_url"),
        content_hash=snapshot["content_hash"],
        fetched_at=(
            datetime.fromisoformat(snapshot["fetched_at"])
            if snapshot.get("fetched_at")
            else None
        ),
        repeated=repeated,
    )


class LocalSourceImportService:
    MAX_PDF_BYTES = 20 * 1024 * 1024
    MAX_PDF_PAGES = 200
    MAX_TEXT_BYTES = 1_000_000

    def __init__(self, engine: Engine, raw_root: Path) -> None:
        self.engine = engine
        self.raw_root = raw_root

    def import_text(
        self,
        *,
        user_id: str,
        topic_id: str,
        title: str,
        content: str,
        request_key: str,
        now: datetime | None = None,
    ) -> SourceImport | SourceImportError:
        import unicodedata

        normalized = unicodedata.normalize("NFC", content).replace("\r\n", "\n").replace("\r", "\n")
        encoded = normalized.encode("utf-8")
        if not normalized.strip():
            return SourceImportError("text_content_empty")
        if len(encoded) > self.MAX_TEXT_BYTES:
            return SourceImportError("text_content_too_large")
        return self._import_content(
            user_id=user_id,
            topic_id=topic_id,
            title=title,
            content=encoded,
            input_type="paste_text",
            mime_type="text/plain",
            page_count=1,
            suffix=".txt",
            request_key=request_key,
            now=now,
        )

    def import_pdf(
        self,
        *,
        user_id: str,
        topic_id: str,
        title: str,
        content: bytes,
        request_key: str,
        now: datetime | None = None,
    ) -> SourceImport | SourceImportError:
        if not content.startswith(b"%PDF-"):
            return SourceImportError("pdf_invalid")
        if len(content) > self.MAX_PDF_BYTES:
            return SourceImportError("pdf_content_too_large")
        try:
            document = parse_pdf(content, title=title)
        except Exception:
            return SourceImportError("pdf_invalid")
        if len(document.pages) > self.MAX_PDF_PAGES:
            return SourceImportError("pdf_page_limit_exceeded")
        if not any(block.text.strip() for page in document.pages for block in page.blocks):
            return SourceImportError("pdf_text_not_found")
        return self._import_content(
            user_id=user_id,
            topic_id=topic_id,
            title=title,
            content=content,
            input_type="pdf_upload",
            mime_type="application/pdf",
            page_count=len(document.pages),
            suffix=".pdf",
            request_key=request_key,
            now=now,
        )

    def _import_content(
        self,
        *,
        user_id: str,
        topic_id: str,
        title: str,
        content: bytes,
        input_type: str,
        mime_type: str,
        page_count: int,
        suffix: str,
        request_key: str,
        now: datetime | None,
    ) -> SourceImport | SourceImportError:
        normalized_key = request_key.strip()
        if not normalized_key:
            return SourceImportError("request_key_required")
        content_hash = hashlib.sha256(content).hexdigest()
        request_hash = hashlib.sha256(
            f"{topic_id}\0{input_type}\0{content_hash}".encode("utf-8")
        ).hexdigest()
        existing_request = self._find_request(user_id, normalized_key)
        if existing_request is not None:
            if existing_request.request_hash != request_hash:
                return SourceImportError("idempotency_key_conflict")
            return _result(existing_request, repeated=True)
        if not self._owns_topic(user_id, topic_id):
            return SourceImportError("topic_not_found")
        candidate_id = str(
            uuid5(NAMESPACE_URL, f"knowledge:content-source:{topic_id}:{content_hash}")
        )
        existing_source = self._find_source(user_id, candidate_id)
        active_now = now or _utc_now()
        if existing_source is not None:
            return self._remember_replay(
                user_id=user_id,
                request_key=normalized_key,
                request_hash=request_hash,
                existing=existing_source,
                now=active_now,
            )
        storage_path = _write_content_addressed(
            self.raw_root, content_hash, content, suffix=suffix
        )
        return self._persist(
            user_id=user_id,
            topic_id=topic_id,
            title=title.strip()[:512] or "Untitled source",
            input_type=input_type,
            mime_type=mime_type,
            page_count=page_count,
            content_hash=content_hash,
            content_size=len(content),
            storage_path=storage_path,
            candidate_id=candidate_id,
            request_key=normalized_key,
            request_hash=request_hash,
            now=active_now,
        )

    def _persist(self, **values: Any) -> SourceImport | SourceImportError:
        source_id, revision_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
        snapshot = {
            "input_type": values["input_type"],
            "title": values["title"],
            "content_hash": values["content_hash"],
            "content_type": values["mime_type"],
        }
        try:
            with self.engine.begin() as connection:
                blob_id = connection.execute(
                    select(content_blobs.c.id).where(
                        content_blobs.c.user_id == values["user_id"],
                        content_blobs.c.content_hash == values["content_hash"],
                    )
                ).scalar_one_or_none()
                if blob_id is None:
                    blob_id = str(uuid4())
                    connection.execute(content_blobs.insert().values(
                        id=blob_id,
                        user_id=values["user_id"],
                        content_hash=values["content_hash"],
                        storage_path=str(values["storage_path"]),
                        byte_size=values["content_size"],
                        created_at=values["now"],
                    ))
                connection.execute(source_documents.insert().values(
                    id=source_id,
                    user_id=values["user_id"],
                    candidate_id=values["candidate_id"],
                    duplicate_of_source_document_id=None,
                    input_type=values["input_type"],
                    title=values["title"],
                    state="active",
                    active_revision_id=None,
                    source_missing=False,
                    version=1,
                    created_at=values["now"],
                    updated_at=values["now"],
                ))
                connection.execute(source_revisions.insert().values(
                    id=revision_id,
                    user_id=values["user_id"],
                    source_document_id=source_id,
                    content_blob_id=blob_id,
                    original_url=None,
                    mime_type=values["mime_type"],
                    page_count=values["page_count"],
                    content_hash=values["content_hash"],
                    sha256=values["content_hash"],
                    active_ingestion_run_id=None,
                    created_at=values["now"],
                ))
                connection.execute(ingestion_runs.insert().values(
                    id=run_id,
                    user_id=values["user_id"],
                    source_document_id=source_id,
                    source_revision_id=revision_id,
                    request_key=values["request_key"],
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
                    created_at=values["now"],
                    updated_at=values["now"],
                ))
                connection.execute(topic_source_documents.insert().values(
                    topic_id=values["topic_id"],
                    source_document_id=source_id,
                    created_at=values["now"],
                ))
                connection.execute(source_import_requests.insert().values(
                    id=str(uuid4()),
                    user_id=values["user_id"],
                    request_key=values["request_key"],
                    request_hash=values["request_hash"],
                    source_document_id=source_id,
                    source_revision_id=revision_id,
                    ingestion_run_id=run_id,
                    created_at=values["now"],
                ))
                connection.execute(outbox_events.insert().values(
                    id=str(uuid4()),
                    aggregate_type="ingestion_run",
                    aggregate_id=run_id,
                    event_type="ingestion.run.queued",
                    payload={"run_id": run_id, "checkpoint": "parsing"},
                    status="pending",
                    attempts=0,
                    available_at=values["now"],
                    locked_by=None,
                    locked_until=None,
                    created_at=values["now"],
                    updated_at=values["now"],
                    published_at=None,
                    last_error=None,
                ))
        except IntegrityError:
            existing = self._find_request(values["user_id"], values["request_key"])
            if existing is not None:
                if existing.request_hash != values["request_hash"]:
                    return SourceImportError("idempotency_key_conflict")
                return _result(existing, repeated=True)
            concurrent = self._find_source(values["user_id"], values["candidate_id"])
            if concurrent is not None:
                return self._remember_replay(
                    user_id=values["user_id"],
                    request_key=values["request_key"],
                    request_hash=values["request_hash"],
                    existing=concurrent,
                    now=values["now"],
                )
            raise
        persisted = self._find_request(values["user_id"], values["request_key"])
        if persisted is None:
            raise RuntimeError("local source import transaction did not persist")
        return _result(persisted, repeated=False)

    def _owns_topic(self, user_id: str, topic_id: str) -> bool:
        with self.engine.connect() as connection:
            return connection.execute(select(topics.c.id).where(
                topics.c.id == topic_id,
                topics.c.user_id == user_id,
                topics.c.archived_at.is_(None),
            )).scalar_one_or_none() is not None

    def _find_source(self, user_id: str, candidate_id: str) -> Any | None:
        with self.engine.connect() as connection:
            return connection.execute(select(
                source_import_requests.c.source_document_id,
                source_import_requests.c.source_revision_id,
                source_import_requests.c.ingestion_run_id,
                source_import_requests.c.request_hash,
                ingestion_runs.c.config_snapshot,
            ).select_from(source_documents.join(
                source_import_requests,
                source_import_requests.c.source_document_id == source_documents.c.id,
            ).join(
                ingestion_runs,
                ingestion_runs.c.id == source_import_requests.c.ingestion_run_id,
            )).where(
                source_documents.c.user_id == user_id,
                source_documents.c.candidate_id == candidate_id,
                source_documents.c.duplicate_of_source_document_id.is_(None),
            ).order_by(source_import_requests.c.created_at).limit(1)).one_or_none()

    def _find_request(self, user_id: str, request_key: str) -> Any | None:
        with self.engine.connect() as connection:
            return connection.execute(select(
                source_import_requests.c.source_document_id,
                source_import_requests.c.source_revision_id,
                source_import_requests.c.ingestion_run_id,
                source_import_requests.c.request_hash,
                ingestion_runs.c.config_snapshot,
            ).join(
                ingestion_runs,
                ingestion_runs.c.id == source_import_requests.c.ingestion_run_id,
            ).where(
                source_import_requests.c.user_id == user_id,
                source_import_requests.c.request_key == request_key,
            )).one_or_none()

    def _remember_replay(
        self,
        *,
        user_id: str,
        request_key: str,
        request_hash: str,
        existing: Any,
        now: datetime,
    ) -> SourceImport | SourceImportError:
        try:
            with self.engine.begin() as connection:
                connection.execute(source_import_requests.insert().values(
                    id=str(uuid4()),
                    user_id=user_id,
                    request_key=request_key,
                    request_hash=request_hash,
                    source_document_id=existing.source_document_id,
                    source_revision_id=existing.source_revision_id,
                    ingestion_run_id=existing.ingestion_run_id,
                    created_at=now,
                ))
        except IntegrityError:
            replay = self._find_request(user_id, request_key)
            if replay is None:
                raise
            if replay.request_hash != request_hash:
                return SourceImportError("idempotency_key_conflict")
            return _result(replay, repeated=True)
        replay = self._find_request(user_id, request_key)
        if replay is None:
            raise RuntimeError("local source replay did not persist")
        return _result(replay, repeated=True)


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
        normalized_url = _normalize_url(url)
        if not normalized_key:
            return SourceImportError("request_key_required")
        candidate_id = _source_candidate_id(topic_id, normalized_url)
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
        existing_source = self._find_source_identity(
            user_id=user_id,
            candidate_id=candidate_id,
        )
        if existing_source is not None:
            return self._remember_replay(
                user_id=user_id,
                request_key=normalized_key,
                request_hash=request_hash,
                existing=existing_source,
                now=now or _utc_now(),
            )

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
            candidate_id=candidate_id,
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
        candidate_id: str,
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
                        candidate_id=candidate_id,
                        duplicate_of_source_document_id=None,
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
            if existing is not None:
                if existing.request_hash != request_hash:
                    return SourceImportError("idempotency_key_conflict")
                return _result(existing, repeated=True)
            concurrent_source = self._find_source_identity(
                user_id=user_id,
                candidate_id=candidate_id,
            )
            if concurrent_source is not None:
                return self._remember_replay(
                    user_id=user_id,
                    request_key=request_key,
                    request_hash=request_hash,
                    existing=concurrent_source,
                    now=now,
                )
            raise
        row = self._find_request(user_id=user_id, request_key=request_key)
        if row is None:
            raise RuntimeError("source import transaction did not persist")
        return _result(row, repeated=False)

    def _remember_replay(
        self,
        *,
        user_id: str,
        request_key: str,
        request_hash: str,
        existing: Any,
        now: datetime,
    ) -> SourceImport | SourceImportError:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    source_import_requests.insert().values(
                        id=str(uuid4()),
                        user_id=user_id,
                        request_key=request_key,
                        request_hash=request_hash,
                        source_document_id=existing.source_document_id,
                        source_revision_id=existing.source_revision_id,
                        ingestion_run_id=existing.ingestion_run_id,
                        created_at=now,
                    )
                )
        except IntegrityError:
            replay = self._find_request(user_id=user_id, request_key=request_key)
            if replay is None:
                raise
            if replay.request_hash != request_hash:
                return SourceImportError("idempotency_key_conflict")
            return _result(replay, repeated=True)
        replay = self._find_request(user_id=user_id, request_key=request_key)
        if replay is None:
            raise RuntimeError("source import replay did not persist")
        return _result(replay, repeated=True)

    def _find_source_identity(self, *, user_id: str, candidate_id: str) -> Any | None:
        with self.engine.connect() as connection:
            return connection.execute(
                select(
                    source_import_requests.c.source_document_id,
                    source_import_requests.c.source_revision_id,
                    source_import_requests.c.ingestion_run_id,
                    source_import_requests.c.request_hash,
                    ingestion_runs.c.config_snapshot,
                )
                .select_from(
                    source_documents.join(
                        source_import_requests,
                        source_import_requests.c.source_document_id == source_documents.c.id,
                    ).join(
                        ingestion_runs,
                        ingestion_runs.c.id == source_import_requests.c.ingestion_run_id,
                    )
                )
                .where(
                    source_documents.c.user_id == user_id,
                    source_documents.c.candidate_id == candidate_id,
                    source_documents.c.duplicate_of_source_document_id.is_(None),
                )
                .order_by(source_import_requests.c.created_at)
                .limit(1)
            ).one_or_none()

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