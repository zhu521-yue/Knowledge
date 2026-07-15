from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.source_tables import topic_source_documents, topics


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class Topic:
    id: str
    user_id: str
    name: str
    description: str
    language: str
    query_profile: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True)
class TopicError:
    code: str


def _from_row(row: Any) -> Topic:
    return Topic(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        description=row.description,
        language=row.language,
        query_profile=dict(row.query_profile),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
    )


class TopicService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(
        self,
        *,
        user_id: str,
        name: str,
        description: str = "",
        language: str = "zh-CN",
        query_profile: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> Topic | TopicError:
        normalized_name = name.strip()
        if not normalized_name:
            return TopicError("topic_name_required")
        active_now = now or utc_now()
        topic_id = str(uuid4())
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    topics.insert().values(
                        id=topic_id,
                        user_id=user_id,
                        name=normalized_name,
                        description=description.strip(),
                        language=language.strip() or "zh-CN",
                        query_profile=query_profile or {},
                        version=1,
                        created_at=active_now,
                        updated_at=active_now,
                        archived_at=None,
                    )
                )
                row = connection.execute(
                    select(topics).where(topics.c.id == topic_id)
                ).one()
        except IntegrityError:
            return TopicError("topic_name_taken")
        return _from_row(row)

    def list_for_user(self, *, user_id: str, include_archived: bool = False) -> list[Topic]:
        query = select(topics).where(topics.c.user_id == user_id)
        if not include_archived:
            query = query.where(topics.c.archived_at.is_(None))
        query = query.order_by(topics.c.created_at, topics.c.id)
        with self.engine.connect() as connection:
            rows = connection.execute(query).all()
        return [_from_row(row) for row in rows]

    def get(self, *, user_id: str, topic_id: str) -> Topic | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(topics).where(
                    topics.c.id == topic_id,
                    topics.c.user_id == user_id,
                )
            ).one_or_none()
        return _from_row(row) if row is not None else None

    def update(
        self,
        *,
        user_id: str,
        topic_id: str,
        expected_version: int,
        changes: dict[str, Any],
        now: datetime | None = None,
    ) -> Topic | TopicError:
        allowed = {"name", "description", "language", "query_profile"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if "name" in values:
            values["name"] = str(values["name"]).strip()
            if not values["name"]:
                return TopicError("topic_name_required")
        if "description" in values:
            values["description"] = str(values["description"]).strip()
        if "language" in values:
            values["language"] = str(values["language"]).strip() or "zh-CN"
        values.update(version=expected_version + 1, updated_at=now or utc_now())
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(topics)
                    .where(
                        topics.c.id == topic_id,
                        topics.c.user_id == user_id,
                        topics.c.version == expected_version,
                    )
                    .values(**values)
                )
                if result.rowcount == 0:
                    exists = connection.execute(
                        select(topics.c.version).where(
                            topics.c.id == topic_id,
                            topics.c.user_id == user_id,
                        )
                    ).scalar_one_or_none()
                    return TopicError(
                        "topic_not_found" if exists is None else "topic_version_conflict"
                    )
                row = connection.execute(
                    select(topics).where(topics.c.id == topic_id)
                ).one()
        except IntegrityError:
            return TopicError("topic_name_taken")
        return _from_row(row)

    def archive(
        self,
        *,
        user_id: str,
        topic_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> Topic | TopicError:
        active_now = now or utc_now()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(topics)
                .where(
                    topics.c.id == topic_id,
                    topics.c.user_id == user_id,
                    topics.c.version == expected_version,
                    topics.c.archived_at.is_(None),
                )
                .values(
                    archived_at=active_now,
                    updated_at=active_now,
                    version=expected_version + 1,
                )
            )
            if result.rowcount == 0:
                current = connection.execute(
                    select(topics.c.version, topics.c.archived_at).where(
                        topics.c.id == topic_id,
                        topics.c.user_id == user_id,
                    )
                ).one_or_none()
                if current is None:
                    return TopicError("topic_not_found")
                if current.archived_at is not None:
                    return TopicError("topic_already_archived")
                return TopicError("topic_version_conflict")
            row = connection.execute(
                select(topics).where(topics.c.id == topic_id)
            ).one()
        return _from_row(row)

    def remove(self, *, user_id: str, topic_id: str) -> bool:
        with self.engine.begin() as connection:
            owned = connection.execute(
                select(topics.c.id).where(
                    topics.c.id == topic_id,
                    topics.c.user_id == user_id,
                )
            ).scalar_one_or_none()
            if owned is None:
                return False
            connection.execute(
                delete(topic_source_documents).where(
                    topic_source_documents.c.topic_id == topic_id
                )
            )
            connection.execute(delete(topics).where(topics.c.id == topic_id))
        return True