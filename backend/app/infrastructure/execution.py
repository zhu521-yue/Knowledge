from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.execution_tables import (
    idempotency_records,
    operations,
    outbox_events,
    worker_jobs,
)

JsonObject = dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _active_utc_second(value: datetime | None) -> datetime:
    active = value or utc_now()
    if active.tzinfo is None:
        raise ValueError("execution timestamps must include a timezone")
    return active.astimezone(UTC).replace(microsecond=0)


def stable_request_hash(payload: Mapping[str, Any] | str | bytes) -> str:
    if isinstance(payload, bytes):
        normalized = payload
    elif isinstance(payload, str):
        normalized = payload.encode("utf-8")
    else:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    return hashlib.sha256(normalized).hexdigest()


@dataclass(frozen=True)
class IdempotencyReservation:
    decision: str
    record_id: str
    operation_id: str | None
    response_status: int | None = None
    response_body: JsonObject | None = None


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    job_type: str
    payload: JsonObject
    attempts: int
    lease_owner: str
    leased_until: datetime


@dataclass(frozen=True)
class OutboxClaim:
    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: JsonObject
    locked_by: str
    locked_until: datetime


class ExecutionRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_operation(
        self,
        *,
        status: str = "pending",
        resource_type: str | None = None,
        resource_id: str | None = None,
        resource_url: str | None = None,
        now: datetime | None = None,
    ) -> str:
        operation_id = str(uuid4())
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            connection.execute(
                operations.insert().values(
                    id=operation_id,
                    status=status,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    resource_url=resource_url,
                    error_snapshot=None,
                    created_at=active_now,
                    updated_at=active_now,
                )
            )
        return operation_id

    def _existing_idempotency_reservation(
        self,
        existing: Mapping[str, Any],
        request_hash: str,
    ) -> IdempotencyReservation:
        if existing["request_hash"] != request_hash:
            return IdempotencyReservation(
                decision="conflict",
                record_id=existing["id"],
                operation_id=existing["operation_id"],
            )
        if existing["status"] == "in_progress":
            return IdempotencyReservation(
                decision="in_progress",
                record_id=existing["id"],
                operation_id=existing["operation_id"],
            )
        return IdempotencyReservation(
            decision="replay",
            record_id=existing["id"],
            operation_id=existing["operation_id"],
            response_status=existing["response_status"],
            response_body=existing["response_body"],
        )

    def reserve_idempotency(
        self,
        *,
        user_id: str,
        command_name: str,
        idempotency_key: str,
        request_hash: str,
        operation_id: str | None = None,
        ttl: timedelta = timedelta(days=7),
        now: datetime | None = None,
    ) -> IdempotencyReservation:
        active_now = _active_utc_second(now)
        identity = and_(
            idempotency_records.c.user_id == user_id,
            idempotency_records.c.command_name == command_name,
            idempotency_records.c.idempotency_key == idempotency_key,
        )
        with self.engine.begin() as connection:
            existing = (
                connection.execute(select(idempotency_records).where(identity))
                .mappings()
                .one_or_none()
            )
        if existing is not None:
            return self._existing_idempotency_reservation(existing, request_hash)

        record_id = str(uuid4())
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    idempotency_records.insert().values(
                        id=record_id,
                        user_id=user_id,
                        command_name=command_name,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        status="in_progress",
                        operation_id=operation_id,
                        response_status=None,
                        response_body=None,
                        created_at=active_now,
                        updated_at=active_now,
                        expires_at=active_now + ttl,
                    )
                )
        except IntegrityError as error:
            with self.engine.begin() as connection:
                concurrent = (
                    connection.execute(
                        select(idempotency_records).where(identity).with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
            if concurrent is None:
                raise error
            return self._existing_idempotency_reservation(concurrent, request_hash)

        return IdempotencyReservation(
            decision="created",
            record_id=record_id,
            operation_id=operation_id,
        )

    def complete_idempotency(
        self,
        *,
        record_id: str,
        response_status: int,
        response_body: JsonObject,
        final_status: str = "succeeded",
        now: datetime | None = None,
    ) -> None:
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            connection.execute(
                update(idempotency_records)
                .where(idempotency_records.c.id == record_id)
                .values(
                    status=final_status,
                    response_status=response_status,
                    response_body=response_body,
                    updated_at=active_now,
                )
            )

    def enqueue_job(
        self,
        *,
        job_type: str,
        payload: JsonObject,
        priority: int = 0,
        max_attempts: int = 3,
        idempotency_record_id: str | None = None,
        available_at: datetime | None = None,
        now: datetime | None = None,
    ) -> str:
        active_now = _active_utc_second(now)
        job_id = str(uuid4())
        with self.engine.begin() as connection:
            connection.execute(
                worker_jobs.insert().values(
                    id=job_id,
                    job_type=job_type,
                    payload=payload,
                    status="queued",
                    priority=priority,
                    attempts=0,
                    max_attempts=max_attempts,
                    lease_owner=None,
                    leased_until=None,
                    idempotency_record_id=idempotency_record_id,
                    available_at=(
                        _active_utc_second(available_at)
                        if available_at is not None
                        else active_now
                    ),
                    created_at=active_now,
                    updated_at=active_now,
                    started_at=None,
                    finished_at=None,
                    last_error=None,
                )
            )
        return job_id

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> JobClaim | None:
        active_now = _active_utc_second(now)
        lease_until = active_now + timedelta(seconds=lease_seconds)
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(worker_jobs)
                    .where(
                        or_(
                            and_(
                                worker_jobs.c.status == "queued",
                                worker_jobs.c.available_at <= active_now,
                            ),
                            and_(
                                worker_jobs.c.status == "running",
                                worker_jobs.c.leased_until < active_now,
                                worker_jobs.c.attempts < worker_jobs.c.max_attempts,
                            ),
                        )
                    )
                    .order_by(
                        worker_jobs.c.priority.desc(), worker_jobs.c.created_at.asc()
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )

            if row is None:
                return None

            attempts = int(row["attempts"]) + 1
            started_at = row["started_at"] or active_now
            connection.execute(
                update(worker_jobs)
                .where(worker_jobs.c.id == row["id"])
                .values(
                    status="running",
                    attempts=attempts,
                    lease_owner=worker_id,
                    leased_until=lease_until,
                    started_at=started_at,
                    updated_at=active_now,
                )
            )
            return JobClaim(
                job_id=row["id"],
                job_type=row["job_type"],
                payload=row["payload"],
                attempts=attempts,
                lease_owner=worker_id,
                leased_until=lease_until,
            )

    def renew_job_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(worker_jobs)
                .where(
                    and_(
                        worker_jobs.c.id == job_id,
                        worker_jobs.c.status == "running",
                        worker_jobs.c.lease_owner == worker_id,
                        worker_jobs.c.leased_until >= active_now,
                    )
                )
                .values(
                    leased_until=active_now + timedelta(seconds=lease_seconds),
                    updated_at=active_now,
                )
            )
            if result.rowcount == 1:
                return True
            current_owner = connection.execute(
                select(worker_jobs.c.lease_owner).where(
                    and_(
                        worker_jobs.c.id == job_id,
                        worker_jobs.c.status == "running",
                        worker_jobs.c.lease_owner == worker_id,
                        worker_jobs.c.leased_until >= active_now,
                    )
                )
            ).scalar_one_or_none()
        return current_owner == worker_id

    def release_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool:
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(worker_jobs)
                .where(
                    and_(
                        worker_jobs.c.id == job_id,
                        worker_jobs.c.status == "running",
                        worker_jobs.c.lease_owner == worker_id,
                        worker_jobs.c.leased_until >= active_now,
                    )
                )
                .values(
                    status="queued",
                    lease_owner=None,
                    leased_until=None,
                    available_at=active_now,
                    updated_at=active_now,
                )
            )
        return result.rowcount == 1

    def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool:
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(worker_jobs)
                .where(
                    and_(
                        worker_jobs.c.id == job_id,
                        worker_jobs.c.status == "running",
                        worker_jobs.c.lease_owner == worker_id,
                        worker_jobs.c.leased_until >= active_now,
                    )
                )
                .values(
                    status="succeeded",
                    lease_owner=None,
                    leased_until=None,
                    finished_at=active_now,
                    updated_at=active_now,
                )
            )
        return result.rowcount == 1

    def fail_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error: JsonObject,
        retry_after: timedelta = timedelta(seconds=30),
        now: datetime | None = None,
    ) -> bool:
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(worker_jobs)
                    .where(
                        and_(
                            worker_jobs.c.id == job_id,
                            worker_jobs.c.status == "running",
                            worker_jobs.c.lease_owner == worker_id,
                            worker_jobs.c.leased_until >= active_now,
                        )
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return False

            exhausted = int(row["attempts"]) >= int(row["max_attempts"])
            connection.execute(
                update(worker_jobs)
                .where(
                    and_(
                        worker_jobs.c.id == job_id,
                        worker_jobs.c.status == "running",
                        worker_jobs.c.lease_owner == worker_id,
                    )
                )
                .values(
                    status="failed" if exhausted else "queued",
                    lease_owner=None,
                    leased_until=None,
                    available_at=active_now + retry_after,
                    finished_at=active_now if exhausted else None,
                    last_error=error,
                    updated_at=active_now,
                )
            )
        return True

    def append_outbox_event(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: JsonObject,
        now: datetime | None = None,
    ) -> str:
        active_now = _active_utc_second(now)
        event_id = str(uuid4())
        with self.engine.begin() as connection:
            connection.execute(
                outbox_events.insert().values(
                    id=event_id,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    event_type=event_type,
                    payload=payload,
                    status="pending",
                    attempts=0,
                    available_at=active_now,
                    locked_by=None,
                    locked_until=None,
                    created_at=active_now,
                    updated_at=active_now,
                    published_at=None,
                    last_error=None,
                )
            )
        return event_id

    def claim_next_outbox_event(
        self,
        *,
        publisher_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> OutboxClaim | None:
        active_now = _active_utc_second(now)
        locked_until = active_now + timedelta(seconds=lease_seconds)
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(outbox_events)
                    .where(
                        or_(
                            and_(
                                outbox_events.c.status == "pending",
                                outbox_events.c.available_at <= active_now,
                            ),
                            and_(
                                outbox_events.c.status == "publishing",
                                outbox_events.c.locked_until < active_now,
                            ),
                        )
                    )
                    .order_by(outbox_events.c.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )

            if row is None:
                return None

            connection.execute(
                update(outbox_events)
                .where(outbox_events.c.id == row["id"])
                .values(
                    status="publishing",
                    attempts=int(row["attempts"]) + 1,
                    locked_by=publisher_id,
                    locked_until=locked_until,
                    updated_at=active_now,
                )
            )
            return OutboxClaim(
                event_id=row["id"],
                aggregate_type=row["aggregate_type"],
                aggregate_id=row["aggregate_id"],
                event_type=row["event_type"],
                payload=row["payload"],
                locked_by=publisher_id,
                locked_until=locked_until,
            )

    def mark_outbox_published(
        self,
        *,
        event_id: str,
        publisher_id: str,
        now: datetime | None = None,
    ) -> bool:
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(outbox_events)
                .where(
                    and_(
                        outbox_events.c.id == event_id,
                        outbox_events.c.status == "publishing",
                        outbox_events.c.locked_by == publisher_id,
                        outbox_events.c.locked_until >= active_now,
                    )
                )
                .values(
                    status="published",
                    locked_by=None,
                    locked_until=None,
                    published_at=active_now,
                    updated_at=active_now,
                )
            )
        return result.rowcount == 1

    def mark_outbox_failed(
        self,
        *,
        event_id: str,
        publisher_id: str,
        error: JsonObject,
        retry_after: timedelta = timedelta(seconds=30),
        now: datetime | None = None,
    ) -> bool:
        active_now = _active_utc_second(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                update(outbox_events)
                .where(
                    and_(
                        outbox_events.c.id == event_id,
                        outbox_events.c.status == "publishing",
                        outbox_events.c.locked_by == publisher_id,
                        outbox_events.c.locked_until >= active_now,
                    )
                )
                .values(
                    status="pending",
                    available_at=active_now + retry_after,
                    locked_by=None,
                    locked_until=None,
                    last_error=error,
                    updated_at=active_now,
                )
            )
        return result.rowcount == 1
