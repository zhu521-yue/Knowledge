from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from app.config import Settings
from app.infrastructure.execution_tables import execution_metadata, worker_jobs
from app.infrastructure.identity import IdentityService
from app.infrastructure.identity_tables import identity_metadata
from app.infrastructure.source_lifecycle import SourceLifecycleError, SourceLifecycleService
from app.infrastructure.source_tables import (
    ingestion_runs,
    source_documents,
    source_lifecycle_events,
)
from app.main import create_app


def make_service() -> tuple[SourceLifecycleService, str, str]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    execution_metadata.create_all(engine)
    identity_metadata.create_all(engine)
    user = IdentityService(engine).bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    source_id = "source-1"
    with engine.begin() as connection:
        connection.execute(
            source_documents.insert().values(
                id=source_id,
                user_id=user.id,
                candidate_id=None,
                input_type="web_url",
                title="Lifecycle",
                state="active",
                active_revision_id=None,
                source_missing=False,
                version=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
                trashed_at=None,
                purge_after=None,
                purged_at=None,
                lifecycle_actor_user_id=None,
                lifecycle_reason=None,
            )
        )
    return SourceLifecycleService(engine), user.id, source_id


def test_archive_restore_trash_and_purge_are_audited_and_versioned() -> None:
    service, user_id, source_id = make_service()
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

    archived = service.command(
        user_id=user_id, source_id=source_id, command="archive",
        expected_version=1, request_key="archive-1", reason="暂不使用", now=now,
    )
    replayed = service.command(
        user_id=user_id, source_id=source_id, command="archive",
        expected_version=1, request_key="archive-1", reason="暂不使用", now=now,
    )
    restored = service.command(
        user_id=user_id, source_id=source_id, command="restore",
        expected_version=2, request_key="restore-1", now=now,
    )
    trashed = service.command(
        user_id=user_id, source_id=source_id, command="trash",
        expected_version=3, request_key="trash-1", reason="不再需要", now=now,
    )
    purging = service.command(
        user_id=user_id, source_id=source_id, command="purge",
        expected_version=4, request_key="purge-1", reason="立即清理", now=now,
    )

    assert archived.state == "archived"
    assert replayed.version == 2
    assert restored.state == "active"
    assert trashed.state == "trashed"
    assert trashed.purge_after == now + timedelta(days=30)
    assert purging.state == "purging"
    with service.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(source_lifecycle_events)) == 4
        jobs = connection.execute(
            select(worker_jobs).order_by(worker_jobs.c.available_at)
        ).all()
    assert [job.job_type for job in jobs] == ["source.purge", "source.purge_due"]
    assert [job.status for job in jobs] == ["queued", "cancelled"]
    assert all(job.payload["source_document_id"] == source_id for job in jobs)
    assert jobs[1].available_at == (now + timedelta(days=30)).replace(tzinfo=None)


def test_trash_requests_cancellation_and_rejects_stale_or_foreign_access() -> None:
    service, user_id, source_id = make_service()
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    with service.engine.begin() as connection:
        connection.execute(
            ingestion_runs.insert().values(
                id="run-1", user_id=user_id, source_document_id=source_id,
                source_revision_id="missing", request_key="run", status="queued",
                checkpoint="parsing", progress=0, parser_version="v", chunking_version="v",
                embedding_index_version="v", sparse_index_version="v", config_snapshot={},
                last_error=None, version=1, started_at=None, published_at=None,
                created_at=now, updated_at=now,
            )
        )

    trashed = service.command(
        user_id=user_id, source_id=source_id, command="trash",
        expected_version=1, request_key="trash-1", now=now,
    )
    stale = service.command(
        user_id=user_id, source_id=source_id, command="restore",
        expected_version=1, request_key="restore-stale", now=now,
    )
    foreign = service.get(user_id="foreign", source_id=source_id)

    assert trashed.state == "trashed"
    assert isinstance(stale, SourceLifecycleError)
    assert stale.code == "source_version_conflict"
    assert foreign is None
    with service.engine.connect() as connection:
        assert connection.scalar(select(ingestion_runs.c.status)) == "cancel_requested"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'lifecycle.db'}",
        storage_notes_path=tmp_path / "notes", storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw", storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports", storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
        provider_credentials_master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def test_lifecycle_api_requires_session_preconditions_and_isolation(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        execution_metadata.create_all(app.state.database_engine)
        identity_metadata.create_all(app.state.database_engine)
        client.post("/auth/bootstrap-admin", json={
            "email": "admin@example.test", "password": "correct horse battery staple",
            "display_name": "Admin",
        })
        client.post("/auth/login", json={
            "email": "admin@example.test", "password": "correct horse battery staple",
        })
        user = client.get("/auth/me").json()["user"]
        now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        with app.state.database_engine.begin() as connection:
            connection.execute(source_documents.insert().values(
                id="source-1", user_id=user["id"], candidate_id=None, input_type="web_url",
                title="Lifecycle", state="active", active_revision_id=None,
                source_missing=False, version=1, created_at=now, updated_at=now,
                archived_at=None, trashed_at=None, purge_after=None, purged_at=None,
                lifecycle_actor_user_id=None, lifecycle_reason=None,
            ))
        missing = client.post("/sources/source-1/archive", json={})
        archived = client.post(
            "/sources/source-1/archive",
            headers={"If-Match": '"1"', "Idempotency-Key": "archive-1"},
            json={"reason": "暂不使用"},
        )
        default_list = client.get("/sources")
        listed = client.get("/sources?state=all")
        hidden = client.get("/sources/missing")

    assert missing.status_code == 428
    assert archived.status_code == 200
    assert archived.json()["source"]["state"] == "archived"
    assert default_list.json()["sources"] == []
    assert listed.json()["sources"][0]["version"] == 2
    assert hidden.status_code == 404