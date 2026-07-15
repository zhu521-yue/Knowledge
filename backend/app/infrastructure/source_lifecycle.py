from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.execution_tables import worker_jobs
from app.infrastructure.source_tables import (
    ingestion_runs,
    source_documents,
    source_lifecycle_commands,
    source_lifecycle_events,
)


@dataclass(frozen=True, slots=True)
class SourceDocumentLifecycle:
    id: str
    title: str
    input_type: str
    state: str
    active_revision_id: str | None
    version: int
    archived_at: datetime | None
    trashed_at: datetime | None
    purge_after: datetime | None
    purged_at: datetime | None
    lifecycle_reason: str | None


@dataclass(frozen=True, slots=True)
class SourceLifecycleError:
    code: str


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _document(row: Any) -> SourceDocumentLifecycle:
    return SourceDocumentLifecycle(
        id=row.id,
        title=row.title,
        input_type=row.input_type,
        state=row.state,
        active_revision_id=row.active_revision_id,
        version=row.version,
        archived_at=_as_utc(row.archived_at),
        trashed_at=_as_utc(row.trashed_at),
        purge_after=_as_utc(row.purge_after),
        purged_at=_as_utc(row.purged_at),
        lifecycle_reason=row.lifecycle_reason,
    )


def _request_hash(source_id: str, expected_version: int, reason: str | None) -> str:
    payload = json.dumps(
        {"source_id": source_id, "version": expected_version, "reason": reason},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _cancel_due_jobs(connection: Any, *, user_id: str, source_id: str, now: datetime) -> None:
    rows = connection.execute(
        select(worker_jobs.c.id, worker_jobs.c.payload).where(
            worker_jobs.c.job_type == "source.purge_due",
            worker_jobs.c.status == "queued",
        )
    ).all()
    job_ids = [
        row.id
        for row in rows
        if row.payload.get("user_id") == user_id
        and row.payload.get("source_document_id") == source_id
    ]
    if job_ids:
        connection.execute(
            update(worker_jobs)
            .where(worker_jobs.c.id.in_(job_ids))
            .values(status="cancelled", updated_at=now, finished_at=now)
        )


def _job_values(
    *, job_type: str, user_id: str, source_id: str, available_at: datetime, now: datetime
) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "job_type": job_type,
        "payload": {"user_id": user_id, "source_document_id": source_id},
        "status": "queued",
        "priority": 10,
        "attempts": 0,
        "max_attempts": 10,
        "lease_owner": None,
        "leased_until": None,
        "idempotency_record_id": None,
        "available_at": available_at,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "last_error": None,
    }


_TRANSITIONS = {
    "archive": ({"active"}, "archived"),
    "restore": ({"archived", "trashed"}, "active"),
    "trash": ({"active", "archived"}, "trashed"),
    "purge": ({"trashed"}, "purging"),
}


class SourceLifecycleService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, *, user_id: str, source_id: str) -> SourceDocumentLifecycle | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(source_documents).where(
                    source_documents.c.id == source_id,
                    source_documents.c.user_id == user_id,
                )
            ).one_or_none()
        return _document(row) if row is not None else None

    def list_for_user(
        self, *, user_id: str, state: str | None = None
    ) -> tuple[SourceDocumentLifecycle, ...]:
        query = select(source_documents).where(
            source_documents.c.user_id == user_id,
            source_documents.c.duplicate_of_source_document_id.is_(None),
        )
        if state is None:
            query = query.where(source_documents.c.state == "active")
        elif state == "all":
            query = query.where(source_documents.c.state != "purged")
        else:
            query = query.where(source_documents.c.state == state)
        with self.engine.connect() as connection:
            rows = connection.execute(
                query.order_by(source_documents.c.updated_at.desc(), source_documents.c.id)
            ).all()
        return tuple(_document(row) for row in rows)

    def command(
        self,
        *,
        user_id: str,
        source_id: str,
        command: str,
        expected_version: int,
        request_key: str,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> SourceDocumentLifecycle | SourceLifecycleError:
        transition = _TRANSITIONS.get(command)
        if transition is None:
            return SourceLifecycleError("source_command_invalid")
        normalized_key = request_key.strip()
        if not normalized_key:
            return SourceLifecycleError("request_key_required")
        normalized_reason = reason.strip()[:512] if reason and reason.strip() else None
        request_hash = _request_hash(source_id, expected_version, normalized_reason)
        active_now = now or utc_now()
        with self.engine.begin() as connection:
            replay = connection.execute(
                select(source_lifecycle_commands).where(
                    source_lifecycle_commands.c.user_id == user_id,
                    source_lifecycle_commands.c.command == command,
                    source_lifecycle_commands.c.request_key == normalized_key,
                )
            ).one_or_none()
            if replay is not None:
                if replay.request_hash != request_hash:
                    return SourceLifecycleError("idempotency_key_conflict")
                row = connection.execute(
                    select(source_documents).where(
                        source_documents.c.id == replay.source_document_id,
                        source_documents.c.user_id == user_id,
                    )
                ).one()
                return _document(row)
            row = connection.execute(
                select(source_documents)
                .where(
                    source_documents.c.id == source_id,
                    source_documents.c.user_id == user_id,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return SourceLifecycleError("source_not_found")
            if row.version != expected_version:
                return SourceLifecycleError("source_version_conflict")
            allowed_states, target_state = transition
            if row.state not in allowed_states:
                return SourceLifecycleError("source_transition_invalid")
            values: dict[str, object] = {
                "state": target_state,
                "version": expected_version + 1,
                "updated_at": active_now,
                "lifecycle_actor_user_id": user_id,
                "lifecycle_reason": normalized_reason,
            }
            if command == "archive":
                values["archived_at"] = active_now
            elif command == "restore":
                values.update(archived_at=None, trashed_at=None, purge_after=None)
                _cancel_due_jobs(
                    connection, user_id=user_id, source_id=source_id, now=active_now
                )
            elif command == "trash":
                purge_after = active_now + timedelta(days=30)
                values.update(trashed_at=active_now, purge_after=purge_after)
                connection.execute(
                    update(ingestion_runs)
                    .where(
                        ingestion_runs.c.user_id == user_id,
                        ingestion_runs.c.source_document_id == source_id,
                        ingestion_runs.c.status.in_(("queued", "running", "validating")),
                    )
                    .values(status="cancel_requested", updated_at=active_now)
                )
                connection.execute(
                    worker_jobs.insert().values(
                        **_job_values(
                            job_type="source.purge_due",
                            user_id=user_id,
                            source_id=source_id,
                            available_at=purge_after,
                            now=active_now,
                        )
                    )
                )
            elif command == "purge":
                _cancel_due_jobs(
                    connection, user_id=user_id, source_id=source_id, now=active_now
                )
                connection.execute(
                    worker_jobs.insert().values(
                        **_job_values(
                            job_type="source.purge",
                            user_id=user_id,
                            source_id=source_id,
                            available_at=active_now,
                            now=active_now,
                        )
                    )
                )
            connection.execute(
                update(source_documents)
                .where(
                    source_documents.c.id == source_id,
                    source_documents.c.user_id == user_id,
                    source_documents.c.version == expected_version,
                )
                .values(**values)
            )
            connection.execute(
                source_lifecycle_events.insert().values(
                    id=str(uuid4()),
                    source_document_id=source_id,
                    user_id=user_id,
                    actor_user_id=user_id,
                    from_state=row.state,
                    to_state=target_state,
                    reason=normalized_reason,
                    created_at=active_now,
                )
            )
            try:
                connection.execute(
                    source_lifecycle_commands.insert().values(
                        id=str(uuid4()),
                        user_id=user_id,
                        source_document_id=source_id,
                        command=command,
                        request_key=normalized_key,
                        request_hash=request_hash,
                        result_version=expected_version + 1,
                        created_at=active_now,
                    )
                )
            except IntegrityError:
                return SourceLifecycleError("idempotency_key_conflict")
            updated = connection.execute(
                select(source_documents).where(source_documents.c.id == source_id)
            ).one()
        return _document(updated)