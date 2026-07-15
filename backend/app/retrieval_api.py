from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import require_current_user
from app.embedding import EmbeddingFailure
from app.infrastructure.identity import IdentityUser
from app.retrieval import RetrievalResult
from app.retrieval_service import RetrieveTopicParents, RetrievalUseCaseError

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


class RetrieveRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=36)
    query: str = Field(min_length=1, max_length=4_000)


def _use_case(request: Request) -> RetrieveTopicParents:
    use_case = getattr(request.app.state, "retrieval_use_case", None)
    if use_case is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="retrieval_not_configured",
        )
    return use_case


@router.post("")
def retrieve(
    payload: RetrieveRequest,
    use_case: Annotated[RetrieveTopicParents, Depends(_use_case)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
) -> dict[str, object]:
    try:
        result = use_case.execute(
            user_id=user.id,
            topic_id=payload.topic_id,
            query=payload.query,
        )
    except RetrievalUseCaseError as error:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if error.code == "topic_not_found"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=status_code, detail=error.code) from error
    except EmbeddingFailure as error:
        status_code = (
            status.HTTP_403_FORBIDDEN
            if error.code in {"embedding_not_authorized", "embedding_credential_missing"}
            else status.HTTP_409_CONFLICT
            if error.code == "embedding_index_version_mismatch"
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=status_code, detail=error.code) from error
    return {"retrieval": _result_response(result)}


def _result_response(result: RetrievalResult) -> dict[str, object]:
    return {
        "retrieval_version": result.retrieval_version,
        "dense_index_version": result.dense_index_version,
        "sparse_index_version": result.sparse_index_version,
        "topic_id": result.topic_id,
        "active_run_ids": list(result.active_run_ids),
        "parents": [
            {
                "parent_chunk_id": parent.parent_chunk_id,
                "source_document_id": parent.source_document_id,
                "source_revision_id": parent.source_revision_id,
                "ingestion_run_id": parent.ingestion_run_id,
                "heading_path": list(parent.heading_path),
                "page_start": parent.page_start,
                "page_end": parent.page_end,
                "text": parent.text,
                "score": parent.score,
                "evidence": [
                    {
                        "child_chunk_id": evidence.child_chunk_id,
                        "text": evidence.text,
                        "page_start": evidence.page_start,
                        "page_end": evidence.page_end,
                        "parent_char_start": evidence.parent_char_start,
                        "parent_char_end": evidence.parent_char_end,
                        "dense_rank": evidence.dense_rank,
                        "sparse_rank": evidence.sparse_rank,
                        "rrf_score": evidence.rrf_score,
                    }
                    for evidence in parent.evidence
                ],
            }
            for parent in result.parents
        ],
    }