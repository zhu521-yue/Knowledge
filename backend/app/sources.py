from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import require_current_user
from app.infrastructure.identity import IdentityUser
from app.infrastructure.source_imports import (
    SourceImport,
    SourceImportError,
    WebSourceImportService,
)
from app.infrastructure.web_fetch import WebFetchError

router = APIRouter(prefix="/sources", tags=["sources"])


class ImportUrlRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=36)
    url: str = Field(min_length=1, max_length=2048)


def _service(request: Request) -> WebSourceImportService:
    return request.app.state.web_source_import_service


def _response(result: SourceImport) -> dict[str, object]:
    return {
        "source": {
            "id": result.source_document_id,
            "title": result.title,
            "input_type": "web_url",
            "state": "active",
            "active_revision_id": None,
            "revision_id": result.source_revision_id,
            "original_url": result.original_url,
            "final_url": result.final_url,
            "content_hash": result.content_hash,
            "fetched_at": result.fetched_at.isoformat(),
        },
        "ingestion_run": {
            "id": result.ingestion_run_id,
            "status": "queued",
            "checkpoint": "parsing",
            "progress": 0,
        },
        "repeated": result.repeated,
    }


def _raise_source_error(error: SourceImportError) -> None:
    status_by_code = {
        "topic_not_found": status.HTTP_404_NOT_FOUND,
        "request_key_required": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "idempotency_key_conflict": status.HTTP_409_CONFLICT,
        "web_content_empty": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "web_content_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    }
    raise HTTPException(
        status_code=status_by_code.get(error.code, status.HTTP_400_BAD_REQUEST),
        detail=error.code,
    )


def _raise_fetch_error(error: WebFetchError) -> None:
    status_by_code = {
        "web_url_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "web_target_not_public": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "web_content_type_unsupported": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "web_content_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
        "web_redirect_limit": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "web_redirect_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "web_response_invalid": status.HTTP_502_BAD_GATEWAY,
        "web_target_unresolved": status.HTTP_502_BAD_GATEWAY,
        "web_fetch_unavailable": status.HTTP_502_BAD_GATEWAY,
        "web_fetch_rejected": status.HTTP_502_BAD_GATEWAY,
    }
    raise HTTPException(
        status_code=status_by_code.get(error.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": error.code, "retryable": error.retryable},
    )


@router.post("/url", status_code=status.HTTP_201_CREATED)
def import_url(
    payload: ImportUrlRequest,
    service: Annotated[WebSourceImportService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    if idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="idempotency_key_required",
        )
    try:
        result = service.import_url(
            user_id=user.id,
            topic_id=payload.topic_id,
            url=payload.url,
            request_key=idempotency_key,
        )
    except WebFetchError as error:
        _raise_fetch_error(error)
    if isinstance(result, SourceImportError):
        _raise_source_error(result)
    return _response(result)