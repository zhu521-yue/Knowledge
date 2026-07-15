from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.engine import Engine

from app.auth import require_current_user
from app.infrastructure.identity import IdentityUser
from app.infrastructure.ingestion_runs import (
    IngestionError,
    IngestionRun,
    IngestionRunService,
)

router = APIRouter(prefix="/ingestion-runs", tags=["ingestion"])


def _service(request: Request) -> IngestionRunService:
    engine: Engine = request.app.state.database_engine
    return IngestionRunService(engine)


def _response(run: IngestionRun) -> dict[str, object]:
    return {
        "id": run.id,
        "source_document_id": run.source_document_id,
        "source_revision_id": run.source_revision_id,
        "status": run.status,
        "checkpoint": run.checkpoint,
        "progress": run.progress,
        "versions": {
            "parser": run.parser_version,
            "chunking": run.chunking_version,
            "embedding_index": run.embedding_index_version,
            "sparse_index": run.sparse_index_version,
        },
        "last_error": run.last_error,
        "version": run.version,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "published_at": run.published_at.isoformat() if run.published_at else None,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _expected_version(if_match: str | None) -> int:
    if if_match is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="if_match_required",
        )
    value = if_match.strip().removeprefix("W/").strip('"')
    try:
        version = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="if_match_invalid",
        ) from exc
    if version < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="if_match_invalid",
        )
    return version


def _raise_error(error: IngestionError) -> None:
    status_by_code = {
        "ingestion_run_not_found": status.HTTP_404_NOT_FOUND,
        "ingestion_version_conflict": status.HTTP_412_PRECONDITION_FAILED,
        "ingestion_not_cancellable": status.HTTP_409_CONFLICT,
        "ingestion_retry_not_allowed": status.HTTP_409_CONFLICT,
        "ingestion_transition_invalid": status.HTTP_409_CONFLICT,
        "request_key_required": status.HTTP_422_UNPROCESSABLE_CONTENT,
    }
    raise HTTPException(
        status_code=status_by_code.get(error.code, status.HTTP_400_BAD_REQUEST),
        detail=error.code,
    )


@router.get("/{run_id}")
def get_ingestion_run(
    run_id: str,
    service: Annotated[IngestionRunService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
) -> dict[str, object]:
    run = service.get(user_id=user.id, run_id=run_id)
    if run is None:
        _raise_error(IngestionError("ingestion_run_not_found"))
    return {"ingestion_run": _response(run)}


@router.post("/{run_id}/cancel")
def cancel_ingestion_run(
    run_id: str,
    service: Annotated[IngestionRunService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, object]:
    result = service.request_cancel(
        user_id=user.id,
        run_id=run_id,
        expected_version=_expected_version(if_match),
    )
    if isinstance(result, IngestionError):
        _raise_error(result)
    return {"ingestion_run": _response(result)}


@router.post("/{run_id}/retry", status_code=status.HTTP_201_CREATED)
def retry_ingestion_run(
    run_id: str,
    service: Annotated[IngestionRunService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    if idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="idempotency_key_required",
        )
    result = service.retry(
        user_id=user.id,
        run_id=run_id,
        request_key=idempotency_key,
    )
    if isinstance(result, IngestionError):
        _raise_error(result)
    return {"ingestion_run": _response(result)}