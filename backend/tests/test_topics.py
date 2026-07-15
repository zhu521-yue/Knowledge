from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from app.config import Settings
from app.infrastructure.identity import IdentityService
from app.infrastructure.identity_tables import identity_metadata
from app.infrastructure.source_tables import (
    source_documents,
    topic_source_documents,
)
from app.infrastructure.topics import TopicError, TopicService
from app.main import create_app


def make_services() -> tuple[IdentityService, TopicService]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    identity_metadata.create_all(engine)
    return IdentityService(engine), TopicService(engine)


def test_topics_are_isolated_by_user_and_name() -> None:
    identity, topics = make_services()
    first_user = identity.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    invitation = identity.create_invitation(actor_user_id=first_user.id, code="TOPICS")
    second_user = identity.register_with_invitation(
        email="member@example.test",
        password="correct horse battery staple",
        display_name="Member",
        invitation_code=invitation.code,
    )

    first = topics.create(user_id=first_user.id, name="Python")
    same_name_other_user = topics.create(user_id=second_user.id, name="Python")
    duplicate = topics.create(user_id=first_user.id, name="Python")

    assert first.name == "Python"
    assert same_name_other_user.name == "Python"
    assert isinstance(duplicate, TopicError)
    assert duplicate.code == "topic_name_taken"
    assert topics.get(user_id=second_user.id, topic_id=first.id) is None
    assert [topic.id for topic in topics.list_for_user(user_id=first_user.id)] == [
        first.id
    ]


def test_topic_updates_use_versions_and_archive_filter() -> None:
    identity, topics = make_services()
    user = identity.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    topic = topics.create(user_id=user.id, name="Python")

    updated = topics.update(
        user_id=user.id,
        topic_id=topic.id,
        expected_version=1,
        changes={"description": "语言基础"},
    )
    stale = topics.update(
        user_id=user.id,
        topic_id=topic.id,
        expected_version=1,
        changes={"description": "覆盖"},
    )
    archived = topics.archive(
        user_id=user.id,
        topic_id=topic.id,
        expected_version=2,
    )

    assert updated.version == 2
    assert updated.description == "语言基础"
    assert isinstance(stale, TopicError)
    assert stale.code == "topic_version_conflict"
    assert archived.archived_at is not None
    assert topics.list_for_user(user_id=user.id) == []
    assert topics.list_for_user(user_id=user.id, include_archived=True)[0].id == topic.id


def test_removing_topic_only_removes_its_source_relation() -> None:
    identity, topics = make_services()
    user = identity.bootstrap_admin(
        email="admin@example.test",
        password="correct horse battery staple",
        display_name="Admin",
    )
    topic = topics.create(user_id=user.id, name="Python")
    now = topic.created_at
    source_id = "source-1"
    with topics.engine.begin() as connection:
        connection.execute(
            source_documents.insert().values(
                id=source_id,
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
            topic_source_documents.insert().values(
                topic_id=topic.id,
                source_document_id=source_id,
                created_at=now,
            )
        )

    assert topics.remove(user_id=user.id, topic_id=topic.id) is True
    with topics.engine.connect() as connection:
        assert connection.execute(
            select(source_documents.c.id).where(source_documents.c.id == source_id)
        ).scalar_one() == source_id
        assert connection.execute(select(topic_source_documents)).all() == []


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'topics.db'}",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
        provider_credentials_master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def test_topic_api_requires_session_and_if_match(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        identity_metadata.create_all(app.state.database_engine)
        unauthorized = client.get("/topics")
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
        created = client.post("/topics", json={"name": "Python"})
        topic_id = created.json()["topic"]["id"]
        missing_precondition = client.patch(
            f"/topics/{topic_id}",
            json={"description": "基础"},
        )
        updated = client.patch(
            f"/topics/{topic_id}",
            headers={"If-Match": '"1"'},
            json={"description": "基础"},
        )
        stale = client.patch(
            f"/topics/{topic_id}",
            headers={"If-Match": '"1"'},
            json={"description": "覆盖"},
        )

    assert unauthorized.status_code == 401
    assert created.status_code == 201
    assert created.json()["topic"]["version"] == 1
    assert missing_precondition.status_code == 428
    assert updated.status_code == 200
    assert updated.json()["topic"]["version"] == 2
    assert stale.status_code == 412