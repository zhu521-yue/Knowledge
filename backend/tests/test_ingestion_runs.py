from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from app.config import Settings
from app.infrastructure.execution_tables import execution_metadata, outbox_events
from app.infrastructure.identity import IdentityService
from app.infrastructure.identity_tables import identity_metadata
from app.infrastructure.ingestion_runs import (
    IngestionError,
    IngestionRunService,
    utc_now,
)
from app.infrastructure.source_tables import (
    content_blobs,
    ingestion_runs,
    source_documents,
    source_revisions,
)
from app.main import create_app


def make_service() -> tuple[IngestionRunService, str, str]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    execution_metadata.create_all(engine)
    identity_metadata.create_all(engine)
    identity = IdentityService(engine)
    user = identity.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    now = user.created_at
    source_document_id = "source-1"
    source_revision_id = "revision-1"
    with engine.begin() as connection:
        connection.execute(
            content_blobs.insert().values(
                id="blob-1",
                user_id=user.id,
                content_hash="a" * 64,
                storage_path="raw/aa/blob",
                byte_size=10,
                created_at=now,
            )
        )
        connection.execute(
            source_documents.insert().values(
                id=source_document_id,
                user_id=user.id,
                candidate_id=None,
                input_type="paste_text",
                title="Example",
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
                id=source_revision_id,
                user_id=user.id,
                source_document_id=source_document_id,
                content_blob_id="blob-1",
                original_url=None,
                mime_type="text/plain",
                page_count=None,
                content_hash="a" * 64,
                sha256=None,
                active_ingestion_run_id=None,
                created_at=now,
            )
        )
    return IngestionRunService(engine), user.id, source_revision_id


def test_create_is_idempotent_and_writes_outbox_atomically() -> None:
    service, user_id, revision_id = make_service()

    created = service.create(
        user_id=user_id,
        source_revision_id=revision_id,
        request_key="request-1",
        config_snapshot={"language": "zh-CN"},
    )
    repeated = service.create(
        user_id=user_id,
        source_revision_id=revision_id,
        request_key="request-1",
        config_snapshot={"language": "ignored-after-first-write"},
    )

    assert created.id == repeated.id
    assert created.status == "queued"
    assert created.config_snapshot == {"language": "zh-CN"}
    with service.engine.connect() as connection:
        assert connection.execute(select(ingestion_runs)).all()
        events = connection.execute(select(outbox_events)).all()
    assert len(events) == 1
    assert events[0].event_type == "ingestion.run.queued"


def test_stage_progress_requires_current_version_and_order() -> None:
    service, user_id, revision_id = make_service()
    run = service.create(
        user_id=user_id,
        source_revision_id=revision_id,
        request_key="request-1",
        config_snapshot={},
    )

    started = service.start(user_id=user_id, run_id=run.id, expected_version=1)
    stale = service.advance(
        user_id=user_id,
        run_id=run.id,
        expected_version=1,
        completed_checkpoint="parsing",
    )
    wrong_stage = service.advance(
        user_id=user_id,
        run_id=run.id,
        expected_version=2,
        completed_checkpoint="chunking",
    )
    parsing_done = service.advance(
        user_id=user_id,
        run_id=run.id,
        expected_version=2,
        completed_checkpoint="parsing",
    )

    assert started.status == "running"
    assert isinstance(stale, IngestionError)
    assert stale.code == "ingestion_version_conflict"
    assert isinstance(wrong_stage, IngestionError)
    assert wrong_stage.code == "ingestion_transition_invalid"
    assert parsing_done.checkpoint == "extracting"
    assert parsing_done.progress == 20


def test_publish_switches_active_run_only_after_validation() -> None:
    service, user_id, revision_id = make_service()
    run = service.create(
        user_id=user_id,
        source_revision_id=revision_id,
        request_key="request-1",
        config_snapshot={},
    )
    current = service.start(user_id=user_id, run_id=run.id, expected_version=1)
    for checkpoint in ("parsing", "extracting", "chunking", "embedding", "validating"):
        current = service.advance(
            user_id=user_id,
            run_id=run.id,
            expected_version=current.version,
            completed_checkpoint=checkpoint,
        )
        with service.engine.connect() as connection:
            active = connection.execute(
                select(source_revisions.c.active_ingestion_run_id).where(
                    source_revisions.c.id == revision_id
                )
            ).scalar_one()
        assert active is None

    published = service.publish(
        user_id=user_id,
        run_id=run.id,
        expected_version=current.version,
    )

    assert published.status == "published"
    assert published.progress == 100
    with service.engine.connect() as connection:
        active = connection.execute(
            select(source_revisions.c.active_ingestion_run_id).where(
                source_revisions.c.id == revision_id
            )
        ).scalar_one()
    assert active == run.id


def test_failure_compensates_without_replacing_previous_active_run() -> None:
    service, user_id, revision_id = make_service()
    first = service.create(
        user_id=user_id,
        source_revision_id=revision_id,
        request_key="first",
        config_snapshot={"parser": "v1"},
    )
    current = service.start(user_id=user_id, run_id=first.id, expected_version=1)
    for checkpoint in ("parsing", "extracting", "chunking", "embedding", "validating"):
        current = service.advance(
            user_id=user_id,
            run_id=first.id,
            expected_version=current.version,
            completed_checkpoint=checkpoint,
        )
    first = service.publish(
        user_id=user_id,
        run_id=first.id,
        expected_version=current.version,
    )

    replacement = service.create(
        user_id=user_id,
        source_revision_id=revision_id,
        request_key="replacement",
        config_snapshot={"parser": "v2"},
    )
    replacement = service.start(
        user_id=user_id,
        run_id=replacement.id,
        expected_version=replacement.version,
    )
    compensating = service.fail(
        user_id=user_id,
        run_id=replacement.id,
        expected_version=replacement.version,
        error={"code": "parser_unavailable", "retryable": True},
    )
    failed = service.complete_compensation(
        user_id=user_id,
        run_id=replacement.id,
        expected_version=compensating.version,
        succeeded=True,
    )
    retried = service.retry(
        user_id=user_id,
        run_id=replacement.id,
        request_key="replacement-retry-1",
    )

    assert failed.status == "failed"
    assert retried.id != replacement.id
    assert retried.config_snapshot == replacement.config_snapshot
    with service.engine.connect() as connection:
        active = connection.execute(
            select(source_revisions.c.active_ingestion_run_id).where(
                source_revisions.c.id == revision_id
            )
        ).scalar_one()
    assert active == first.id


def test_cancelled_run_cannot_publish() -> None:
    service, user_id, revision_id = make_service()
    run = service.create(
        user_id=user_id,
        source_revision_id=revision_id,
        request_key="request-1",
        config_snapshot={},
    )
    run = service.request_cancel(
        user_id=user_id,
        run_id=run.id,
        expected_version=run.version,
    )
    run = service.begin_compensation(
        user_id=user_id,
        run_id=run.id,
        expected_version=run.version,
    )
    run = service.complete_compensation(
        user_id=user_id,
        run_id=run.id,
        expected_version=run.version,
        succeeded=True,
    )
    publish = service.publish(
        user_id=user_id,
        run_id=run.id,
        expected_version=run.version,
    )

    assert run.status == "cancelled"
    assert isinstance(publish, IngestionError)
    assert publish.code == "ingestion_transition_invalid"


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ingestion.db'}",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
        provider_credentials_master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def test_ingestion_api_exposes_progress_and_requires_preconditions(
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        execution_metadata.create_all(app.state.database_engine)
        identity_metadata.create_all(app.state.database_engine)
        unauthorized = client.get("/ingestion-runs/missing")
        created_user = client.post(
            "/auth/bootstrap-admin",
            json={
                "email": "admin@example.test",
                "password": "correct horse battery staple",
                "display_name": "Admin",
            },
        ).json()["user"]
        client.post(
            "/auth/login",
            json={
                "email": "admin@example.test",
                "password": "correct horse battery staple",
            },
        )
        now = utc_now()
        with app.state.database_engine.begin() as connection:
            connection.execute(
                content_blobs.insert().values(
                    id="api-blob",
                    user_id=created_user["id"],
                    content_hash="b" * 64,
                    storage_path="raw/bb/blob",
                    byte_size=10,
                    created_at=now,
                )
            )
            connection.execute(
                source_documents.insert().values(
                    id="api-source",
                    user_id=created_user["id"],
                    candidate_id=None,
                    input_type="paste_text",
                    title="API Example",
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
                    id="api-revision",
                    user_id=created_user["id"],
                    source_document_id="api-source",
                    content_blob_id="api-blob",
                    original_url=None,
                    mime_type="text/plain",
                    page_count=None,
                    content_hash="b" * 64,
                    sha256=None,
                    active_ingestion_run_id=None,
                    created_at=now,
                )
            )
        run = IngestionRunService(app.state.database_engine).create(
            user_id=created_user["id"],
            source_revision_id="api-revision",
            request_key="api-request",
            config_snapshot={},
        )

        progress = client.get(f"/ingestion-runs/{run.id}")
        missing_if_match = client.post(f"/ingestion-runs/{run.id}/cancel")
        cancelled = client.post(
            f"/ingestion-runs/{run.id}/cancel",
            headers={"If-Match": '\"1\"'},
        )

    assert unauthorized.status_code == 401
    assert progress.status_code == 200
    assert progress.json()["ingestion_run"]["progress"] == 0
    assert missing_if_match.status_code == 428
    assert cancelled.status_code == 200
    assert cancelled.json()["ingestion_run"]["status"] == "cancel_requested"