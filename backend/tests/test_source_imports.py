from __future__ import annotations

import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from app.config import Settings
from app.infrastructure.execution_tables import execution_metadata, outbox_events
from app.infrastructure.identity import IdentityService
from app.infrastructure.identity_tables import identity_metadata
from app.infrastructure.source_imports import WebSourceImportService
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