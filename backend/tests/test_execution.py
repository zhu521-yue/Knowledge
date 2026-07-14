from datetime import timedelta

from sqlalchemy import create_engine, select

from app.infrastructure.execution import (
    ExecutionRepository,
    stable_request_hash,
    utc_now,
)
from app.infrastructure.execution_tables import (
    execution_metadata,
    outbox_events,
    worker_jobs,
)


def make_repository() -> ExecutionRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    execution_metadata.create_all(engine)
    return ExecutionRepository(engine)


def test_stable_request_hash_is_deterministic() -> None:
    left = stable_request_hash({"b": [2, 1], "a": 1})
    right = stable_request_hash({"a": 1, "b": [2, 1]})

    assert left == right
    assert left != stable_request_hash({"a": 1, "b": [1, 2]})


def test_idempotency_reservation_conflict_and_replay() -> None:
    repository = make_repository()
    operation_id = repository.create_operation()
    request_hash = stable_request_hash({"source": "upload"})

    created = repository.reserve_idempotency(
        user_id="user_1",
        command_name="source.upload",
        idempotency_key="key_1",
        request_hash=request_hash,
        operation_id=operation_id,
    )
    repeated = repository.reserve_idempotency(
        user_id="user_1",
        command_name="source.upload",
        idempotency_key="key_1",
        request_hash=request_hash,
    )
    conflict = repository.reserve_idempotency(
        user_id="user_1",
        command_name="source.upload",
        idempotency_key="key_1",
        request_hash=stable_request_hash({"source": "other"}),
    )
    repository.complete_idempotency(
        record_id=created.record_id,
        response_status=202,
        response_body={"operation_id": operation_id},
    )
    replay = repository.reserve_idempotency(
        user_id="user_1",
        command_name="source.upload",
        idempotency_key="key_1",
        request_hash=request_hash,
    )

    assert created.decision == "created"
    assert repeated.decision == "in_progress"
    assert repeated.operation_id == operation_id
    assert conflict.decision == "conflict"
    assert replay.decision == "replay"
    assert replay.response_status == 202
    assert replay.response_body == {"operation_id": operation_id}


def test_job_lease_claim_retry_and_completion() -> None:
    repository = make_repository()
    now = utc_now()
    job_id = repository.enqueue_job(
        job_type="noop",
        payload={"value": 1},
        priority=10,
        max_attempts=2,
        now=now,
    )

    first_claim = repository.claim_next_job(
        worker_id="worker_a",
        lease_seconds=30,
        now=now,
    )
    renewed = repository.renew_job_lease(
        job_id=job_id,
        worker_id="worker_a",
        lease_seconds=30,
        now=now + timedelta(seconds=1),
    )
    no_duplicate_claim = repository.claim_next_job(
        worker_id="worker_b",
        lease_seconds=30,
        now=now + timedelta(seconds=31),
    )
    expired_completion = repository.complete_job(
        job_id=job_id,
        worker_id="worker_a",
        now=now + timedelta(seconds=32),
    )
    retry_claim = repository.claim_next_job(
        worker_id="worker_b",
        lease_seconds=30,
        now=now + timedelta(seconds=32),
    )
    stale_completion = repository.complete_job(
        job_id=job_id,
        worker_id="worker_a",
        now=now + timedelta(seconds=33),
    )
    completed = repository.complete_job(
        job_id=job_id,
        worker_id="worker_b",
        now=now + timedelta(seconds=33),
    )
    completed_claim = repository.claim_next_job(
        worker_id="worker_c",
        lease_seconds=30,
        now=now + timedelta(seconds=34),
    )

    assert first_claim is not None
    assert first_claim.job_id == job_id
    assert first_claim.attempts == 1
    assert renewed is True
    assert no_duplicate_claim is None
    assert expired_completion is False
    assert retry_claim is not None
    assert retry_claim.lease_owner == "worker_b"
    assert retry_claim.attempts == 2
    assert stale_completion is False
    assert completed is True
    assert completed_claim is None

    with repository.engine.connect() as connection:
        row = connection.execute(
            select(worker_jobs.c.status).where(worker_jobs.c.id == job_id)
        ).one()
    assert row.status == "succeeded"


def test_job_release_requires_current_lease_owner() -> None:
    repository = make_repository()
    now = utc_now()
    job_id = repository.enqueue_job(job_type="noop", payload={}, now=now)
    claim = repository.claim_next_job(worker_id="worker_a", lease_seconds=30, now=now)

    assert claim is not None
    assert (
        repository.release_job(
            job_id=job_id,
            worker_id="worker_b",
            now=now + timedelta(seconds=1),
        )
        is False
    )
    assert (
        repository.release_job(
            job_id=job_id,
            worker_id="worker_a",
            now=now + timedelta(seconds=1),
        )
        is True
    )

    reclaimed = repository.claim_next_job(
        worker_id="worker_b",
        lease_seconds=30,
        now=now + timedelta(seconds=1),
    )
    assert reclaimed is not None
    assert reclaimed.lease_owner == "worker_b"


def test_job_failure_requeues_until_attempts_are_exhausted() -> None:
    repository = make_repository()
    now = utc_now()
    job_id = repository.enqueue_job(
        job_type="unstable",
        payload={},
        max_attempts=1,
        now=now,
    )
    claim = repository.claim_next_job(worker_id="worker_a", lease_seconds=10, now=now)
    assert claim is not None

    failed = repository.fail_job(
        job_id=job_id,
        worker_id="worker_a",
        error={"code": "boom"},
        now=now,
    )

    assert failed is True
    with repository.engine.connect() as connection:
        row = connection.execute(
            select(worker_jobs).where(worker_jobs.c.id == job_id)
        ).one()
    assert row.status == "failed"
    assert row.last_error == {"code": "boom"}


def test_outbox_event_claim_publish_and_retry() -> None:
    repository = make_repository()
    now = utc_now()
    event_id = repository.append_outbox_event(
        aggregate_type="source",
        aggregate_id="source_1",
        event_type="source.created",
        payload={"source_id": "source_1"},
        now=now,
    )

    claim = repository.claim_next_outbox_event(
        publisher_id="publisher_a",
        lease_seconds=30,
        now=now,
    )
    duplicate_claim = repository.claim_next_outbox_event(
        publisher_id="publisher_b",
        lease_seconds=30,
        now=now + timedelta(seconds=1),
    )
    failed = repository.mark_outbox_failed(
        event_id=event_id,
        publisher_id="publisher_a",
        error={"code": "temporary"},
        retry_after=timedelta(seconds=5),
        now=now + timedelta(seconds=2),
    )
    unavailable_claim = repository.claim_next_outbox_event(
        publisher_id="publisher_b",
        lease_seconds=30,
        now=now + timedelta(seconds=3),
    )
    retry_claim = repository.claim_next_outbox_event(
        publisher_id="publisher_b",
        lease_seconds=30,
        now=now + timedelta(seconds=8),
    )
    stale_publish = repository.mark_outbox_published(
        event_id=event_id,
        publisher_id="publisher_a",
        now=now + timedelta(seconds=9),
    )
    published = repository.mark_outbox_published(
        event_id=event_id,
        publisher_id="publisher_b",
        now=now + timedelta(seconds=9),
    )

    assert claim is not None
    assert claim.event_id == event_id
    assert duplicate_claim is None
    assert failed is True
    assert unavailable_claim is None
    assert retry_claim is not None
    assert retry_claim.locked_by == "publisher_b"
    assert stale_publish is False
    assert published is True

    with repository.engine.connect() as connection:
        row = connection.execute(
            select(outbox_events.c.status).where(outbox_events.c.id == event_id)
        ).one()
    assert row.status == "published"


def test_outbox_expired_lock_cannot_publish_or_retry() -> None:
    repository = make_repository()
    now = utc_now()
    event_id = repository.append_outbox_event(
        aggregate_type="source",
        aggregate_id="source_1",
        event_type="source.created",
        payload={},
        now=now,
    )
    claim = repository.claim_next_outbox_event(
        publisher_id="publisher_a",
        lease_seconds=1,
        now=now,
    )

    assert claim is not None
    assert (
        repository.mark_outbox_published(
            event_id=event_id,
            publisher_id="publisher_a",
            now=now + timedelta(seconds=2),
        )
        is False
    )
    assert (
        repository.mark_outbox_failed(
            event_id=event_id,
            publisher_id="publisher_a",
            error={"code": "late"},
            now=now + timedelta(seconds=2),
        )
        is False
    )

    reclaimed = repository.claim_next_outbox_event(
        publisher_id="publisher_b",
        lease_seconds=30,
        now=now + timedelta(seconds=2),
    )
    assert reclaimed is not None
    assert reclaimed.locked_by == "publisher_b"


def test_execution_metadata_contains_framework_tables() -> None:
    assert {
        "operations",
        "idempotency_records",
        "worker_jobs",
        "outbox_events",
    }.issubset(execution_metadata.tables)
