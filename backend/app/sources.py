from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from app.auth import require_current_user
from app.infrastructure.identity import IdentityUser
from app.infrastructure.source_lifecycle import (
    SourceDocumentLifecycle,
    SourceLifecycleError,
    SourceLifecycleService,
)
from app.infrastructure.source_imports import (
    LocalSourceImportService,
    SourceImport,
    SourceImportError,
    WebSourceImportService,
)
from app.infrastructure.web_fetch import WebFetchError

router = APIRouter(prefix="/sources", tags=["sources"])


class ImportUrlRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=36)
    url: str = Field(min_length=1, max_length=2048)


class ImportTextRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)


class LifecycleRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=512)


def _import_service(request: Request) -> WebSourceImportService:
    return request.app.state.web_source_import_service


def _local_import_service(request: Request) -> LocalSourceImportService:
    return request.app.state.local_source_import_service


def _lifecycle_service(request: Request) -> SourceLifecycleService:
    return SourceLifecycleService(request.app.state.database_engine)


def _expected_version(if_match: str | None) -> int:
    if if_match is None:
        raise HTTPException(status_code=428, detail="if_match_required")
    try:
        value = int(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="if_match_invalid") from exc
    if value < 1:
        raise HTTPException(status_code=400, detail="if_match_invalid")
    return value


def _lifecycle_response(source: SourceDocumentLifecycle) -> dict[str, object]:
    return {
        "id": source.id,
        "title": source.title,
        "input_type": source.input_type,
        "state": source.state,
        "active_revision_id": source.active_revision_id,
        "version": source.version,
        "archived_at": source.archived_at.isoformat() if source.archived_at else None,
        "trashed_at": source.trashed_at.isoformat() if source.trashed_at else None,
        "purge_after": source.purge_after.isoformat() if source.purge_after else None,
        "purged_at": source.purged_at.isoformat() if source.purged_at else None,
        "lifecycle_reason": source.lifecycle_reason,
    }


def _raise_lifecycle_error(error: SourceLifecycleError) -> None:
    status_by_code = {
        "source_not_found": 404,
        "source_version_conflict": 412,
        "source_transition_invalid": 409,
        "idempotency_key_conflict": 409,
        "request_key_required": 422,
    }
    raise HTTPException(status_code=status_by_code.get(error.code, 400), detail=error.code)


def _response(result: SourceImport) -> dict[str, object]:
    return {
        "source": {
            "id": result.source_document_id,
            "title": result.title,
            "input_type": result.input_type,
            "state": "active",
            "active_revision_id": None,
            "revision_id": result.source_revision_id,
            "original_url": result.original_url,
            "final_url": result.final_url,
            "content_hash": result.content_hash,
            "fetched_at": result.fetched_at.isoformat() if result.fetched_at else None,
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
        "text_content_empty": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "text_content_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
        "pdf_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "pdf_content_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
        "pdf_page_limit_exceeded": status.HTTP_413_CONTENT_TOO_LARGE,
        "pdf_text_not_found": status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    service: Annotated[WebSourceImportService, Depends(_import_service)],
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


@router.post("/text", status_code=status.HTTP_201_CREATED)
def import_text(
    payload: ImportTextRequest,
    service: Annotated[LocalSourceImportService, Depends(_local_import_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    if idempotency_key is None:
        raise HTTPException(status_code=428, detail="idempotency_key_required")
    result = service.import_text(
        user_id=user.id,
        topic_id=payload.topic_id,
        title=payload.title,
        content=payload.content,
        request_key=idempotency_key,
    )
    if isinstance(result, SourceImportError):
        _raise_source_error(result)
    return _response(result)


@router.post("/pdf", status_code=status.HTTP_201_CREATED)
async def import_pdf(
    service: Annotated[LocalSourceImportService, Depends(_local_import_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    topic_id: Annotated[str, Form(min_length=1, max_length=36)],
    title: Annotated[str, Form(min_length=1, max_length=512)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    if idempotency_key is None:
        raise HTTPException(status_code=428, detail="idempotency_key_required")
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="pdf_content_type_required")
    content = await file.read(LocalSourceImportService.MAX_PDF_BYTES + 1)
    result = service.import_pdf(
        user_id=user.id,
        topic_id=topic_id,
        title=title,
        content=content,
        request_key=idempotency_key,
    )
    if isinstance(result, SourceImportError):
        _raise_source_error(result)
    return _response(result)


@router.get("")
def list_sources(
    service: Annotated[SourceLifecycleService, Depends(_lifecycle_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    state_filter: Annotated[str | None, Query(alias="state")] = None,
) -> dict[str, object]:
    return {
        "sources": [
            _lifecycle_response(source)
            for source in service.list_for_user(user_id=user.id, state=state_filter)
        ]
    }


@router.get("/{source_id}")
def get_source(
    source_id: str,
    service: Annotated[SourceLifecycleService, Depends(_lifecycle_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
) -> dict[str, object]:
    source = service.get(user_id=user.id, source_id=source_id)
    if source is None:
        _raise_lifecycle_error(SourceLifecycleError("source_not_found"))
    return {"source": _lifecycle_response(source)}


@router.post("/{source_id}/{command}")
def lifecycle_command(
    source_id: str,
    command: str,
    payload: LifecycleRequest,
    service: Annotated[SourceLifecycleService, Depends(_lifecycle_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    if idempotency_key is None:
        raise HTTPException(status_code=428, detail="idempotency_key_required")
    result = service.command(
        user_id=user.id,
        source_id=source_id,
        command=command,
        expected_version=_expected_version(if_match),
        request_key=idempotency_key,
        reason=payload.reason,
    )
    if isinstance(result, SourceLifecycleError):
        _raise_lifecycle_error(result)
    return {"source": _lifecycle_response(result)}
