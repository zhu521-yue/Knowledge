from __future__ import annotations

import socket
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from app.config import Settings
from app.infrastructure.execution_tables import execution_metadata, outbox_events
from app.infrastructure.identity import IdentityService
from app.infrastructure.identity_tables import identity_metadata
from app.infrastructure.source_imports import (
    LocalSourceImportService,
    SourceImport,
    WebSourceImportService,
)
from app.infrastructure.source_tables import (
    content_blobs,
    ingestion_runs,
    source_documents,
    source_import_requests,
    source_revisions,
    topic_source_documents,
)
from app.infrastructure.topics import TopicService
from app.infrastructure.web_fetch import (
    FetchedWebPage,
    WebFetchError,
    validate_public_web_target,
)
from app.main import create_app


class StubFetcher:
    def __init__(self, content: bytes | None = None) -> None:
        self.content = content or b"<html><head><title>Guide</title></head><body><h1>Intro</h1><p>Hello world.</p></body></html>"
        self.calls = 0

    def fetch(self, url: str) -> FetchedWebPage:
        self.calls += 1
        return FetchedWebPage(
            requested_url=url,
            final_url="https://www.example.test/guide",
            content=self.content,
            content_type="text/html",
            fetched_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        )


def make_service(tmp_path: Path) -> tuple[WebSourceImportService, StubFetcher, str, str]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    execution_metadata.create_all(engine)
    identity_metadata.create_all(engine)
    identity = IdentityService(engine)
    user = identity.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    topic = TopicService(engine).create(user_id=user.id, name="Web")
    fetcher = StubFetcher()
    return WebSourceImportService(engine, tmp_path / "raw", fetcher), fetcher, user.id, topic.id


def test_web_import_is_atomic_content_addressed_and_idempotent(tmp_path: Path) -> None:
    service, fetcher, user_id, topic_id = make_service(tmp_path)

    created = service.import_url(
        user_id=user_id,
        topic_id=topic_id,
        url="https://www.example.test/start",
        request_key="request-1",
    )
    repeated = service.import_url(
        user_id=user_id,
        topic_id=topic_id,
        url="https://www.example.test/start",
        request_key="request-1",
    )
    repeated_with_new_key = service.import_url(
        user_id=user_id,
        topic_id=topic_id,
        url="https://WWW.EXAMPLE.TEST:443/start#section",
        request_key="request-2",
    )
    conflict = service.import_url(
        user_id=user_id,
        topic_id=topic_id,
        url="https://different.example.test",
        request_key="request-1",
    )

    assert created.source_document_id == repeated.source_document_id
    assert created.source_document_id == repeated_with_new_key.source_document_id
    assert repeated.repeated is True
    assert repeated_with_new_key.repeated is True
    assert conflict.code == "idempotency_key_conflict"
    assert fetcher.calls == 1
    assert created.title == "Guide"
    with service.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(source_documents)) == 1
        assert connection.scalar(select(func.count()).select_from(source_revisions)) == 1
        assert connection.scalar(select(func.count()).select_from(content_blobs)) == 1
        assert connection.scalar(select(func.count()).select_from(ingestion_runs)) == 1
        assert connection.scalar(select(func.count()).select_from(source_import_requests)) == 2
        assert connection.scalar(select(func.count()).select_from(topic_source_documents)) == 1
        run = connection.execute(select(ingestion_runs)).one()
        document = connection.execute(select(source_documents)).one()
        blob = connection.execute(select(content_blobs)).one()
        event = connection.execute(select(outbox_events)).one()
    assert document.active_revision_id is None
    assert run.status == "queued"
    assert run.config_snapshot["final_url"] == "https://www.example.test/guide"
    assert event.aggregate_id == run.id
    assert Path(blob.storage_path).read_text(encoding="utf-8") == "# Intro\n\nHello world.\n"


def test_text_and_pdf_import_api_use_unified_queue(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        execution_metadata.create_all(app.state.database_engine)
        identity_metadata.create_all(app.state.database_engine)
        client.post(
            "/auth/bootstrap-admin",
            json={
                "email": "local-api@example.test",
                "password": "correct horse battery staple",
                "display_name": "Admin",
            },
        )
        client.post(
            "/auth/login",
            json={
                "email": "local-api@example.test",
                "password": "correct horse battery staple",
            },
        )
        topic = client.post("/topics", json={"name": "Local API"}).json()["topic"]
        text = client.post(
            "/sources/text",
            headers={"Idempotency-Key": "api-text-1"},
            json={
                "topic_id": topic["id"],
                "title": "Pasted notes",
                "content": "# Notes\n\nSearchable text",
            },
        )
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Searchable API PDF")
        pdf_content = pdf.tobytes()
        pdf.close()
        uploaded = client.post(
            "/sources/pdf",
            headers={"Idempotency-Key": "api-pdf-1"},
            data={"topic_id": topic["id"], "title": "Uploaded PDF"},
            files={"file": ("sample.pdf", pdf_content, "application/pdf")},
        )
        wrong_media = client.post(
            "/sources/pdf",
            headers={"Idempotency-Key": "api-pdf-2"},
            data={"topic_id": topic["id"], "title": "Wrong"},
            files={"file": ("sample.txt", b"not a pdf", "text/plain")},
        )

    assert text.status_code == 201
    assert text.json()["source"]["input_type"] == "paste_text"
    assert text.json()["ingestion_run"]["status"] == "queued"
    assert uploaded.status_code == 201
    assert uploaded.json()["source"]["input_type"] == "pdf_upload"
    assert uploaded.json()["ingestion_run"]["status"] == "queued"
    assert wrong_media.status_code == 415
    assert wrong_media.json()["detail"] == "pdf_content_type_required"


def _local_fixture(tmp_path: Path) -> tuple[LocalSourceImportService, str, str]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    execution_metadata.create_all(engine)
    identity_metadata.create_all(engine)
    user = IdentityService(engine).bootstrap_admin(
        email="local@example.test",
        password="correct horse battery staple",
        display_name="Local",
    )
    topic = TopicService(engine).create(user_id=user.id, name="Local imports")
    return LocalSourceImportService(engine, tmp_path / "raw"), user.id, topic.id


def test_paste_text_normalizes_and_reuses_immutable_content(tmp_path: Path) -> None:
    service, user_id, topic_id = _local_fixture(tmp_path)

    first = service.import_text(
        user_id=user_id,
        topic_id=topic_id,
        title="First title",
        content="Cafe\u0301\r\n\r\nKnowledge",
        request_key="text-1",
    )
    repeated = service.import_text(
        user_id=user_id,
        topic_id=topic_id,
        title="Another title",
        content="Café\n\nKnowledge",
        request_key="text-2",
    )

    assert isinstance(first, SourceImport)
    assert isinstance(repeated, SourceImport)
    assert repeated.repeated is True
    assert repeated.source_document_id == first.source_document_id
    assert repeated.source_revision_id == first.source_revision_id
    with service.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(source_documents)) == 1
        assert connection.scalar(select(func.count()).select_from(source_revisions)) == 1
        assert connection.scalar(select(func.count()).select_from(content_blobs)) == 1
        blob = connection.execute(select(content_blobs)).one()
    assert Path(blob.storage_path).suffix == ".txt"
    assert Path(blob.storage_path).read_text(encoding="utf-8") == "Café\n\nKnowledge"


def test_pdf_import_preserves_original_and_rejects_non_text_pdf(tmp_path: Path) -> None:
    service, user_id, topic_id = _local_fixture(tmp_path)
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Searchable PDF")
    content = pdf.tobytes()
    pdf.close()

    imported = service.import_pdf(
        user_id=user_id,
        topic_id=topic_id,
        title="PDF",
        content=content,
        request_key="pdf-1",
    )
    empty_pdf = pymupdf.open()
    empty_pdf.new_page()
    empty_content = empty_pdf.tobytes()
    empty_pdf.close()
    rejected = service.import_pdf(
        user_id=user_id,
        topic_id=topic_id,
        title="Scanned",
        content=empty_content,
        request_key="pdf-2",
    )

    assert isinstance(imported, SourceImport)
    assert imported.input_type == "pdf_upload"
    assert rejected.code == "pdf_text_not_found"
    with service.engine.connect() as connection:
        revision = connection.execute(
            select(source_revisions).where(source_revisions.c.id == imported.source_revision_id)
        ).one()
        blob = connection.execute(
            select(content_blobs).where(content_blobs.c.id == revision.content_blob_id)
        ).one()
    assert revision.page_count == 1
    assert revision.mime_type == "application/pdf"
    assert Path(blob.storage_path).suffix == ".pdf"
    assert Path(blob.storage_path).read_bytes() == content


def test_same_url_in_different_topics_stays_distinct(tmp_path: Path) -> None:
    service, fetcher, user_id, first_topic_id = make_service(tmp_path)
    second_topic = TopicService(service.engine).create(user_id=user_id, name="Other")

    first = service.import_url(
        user_id=user_id,
        topic_id=first_topic_id,
        url="https://www.example.test/start",
        request_key="first-topic",
    )
    second = service.import_url(
        user_id=user_id,
        topic_id=second_topic.id,
        url="https://WWW.EXAMPLE.TEST:443/start#section",
        request_key="second-topic",
    )

    assert first.source_document_id != second.source_document_id
    assert fetcher.calls == 2


def test_web_import_rejects_foreign_topic_before_fetch(tmp_path: Path) -> None:
    service, fetcher, user_id, _topic_id = make_service(tmp_path)

    result = service.import_url(
        user_id=user_id,
        topic_id="not-owned",
        url="https://www.example.test",
        request_key="request-1",
    )

    assert result.code == "topic_not_found"
    assert fetcher.calls == 0


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_target_validation_rejects_non_public_addresses(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )

    with pytest.raises(WebFetchError, match="web_target_not_public"):
        validate_public_web_target("https://example.test/page")


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'sources.db'}",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
        provider_credentials_master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def test_url_import_api_requires_session_key_and_owned_topic(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    fetcher = StubFetcher()
    with TestClient(app) as client:
        execution_metadata.create_all(app.state.database_engine)
        identity_metadata.create_all(app.state.database_engine)
        app.state.web_source_import_service = WebSourceImportService(
            app.state.database_engine, tmp_path / "raw", fetcher
        )
        unauthorized = client.post(
            "/sources/url",
            headers={"Idempotency-Key": "request-1"},
            json={"topic_id": "topic", "url": "https://example.test"},
        )
        client.post(
            "/auth/bootstrap-admin",
            json={
                "email": "admin@example.test",
                "password": "correct horse battery staple",
                "display_name": "Admin",
            },
        )
        client.post(
            "/auth/login",
            json={
                "email": "admin@example.test",
                "password": "correct horse battery staple",
            },
        )
        topic = client.post("/topics", json={"name": "Web"}).json()["topic"]
        missing_key = client.post(
            "/sources/url",
            json={"topic_id": topic["id"], "url": "https://example.test"},
        )
        created = client.post(
            "/sources/url",
            headers={"Idempotency-Key": "request-1"},
            json={"topic_id": topic["id"], "url": "https://example.test"},
        )
        repeated = client.post(
            "/sources/url",
            headers={"Idempotency-Key": "request-1"},
            json={"topic_id": topic["id"], "url": "https://example.test"},
        )
        conflict = client.post(
            "/sources/url",
            headers={"Idempotency-Key": "request-1"},
            json={"topic_id": topic["id"], "url": "https://different.test"},
        )

    assert unauthorized.status_code == 401
    assert missing_key.status_code == 428
    assert created.status_code == 201
    assert created.json()["source"]["active_revision_id"] is None
    assert created.json()["ingestion_run"]["status"] == "queued"
    assert repeated.json()["repeated"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "idempotency_key_conflict"
    assert fetcher.calls == 1