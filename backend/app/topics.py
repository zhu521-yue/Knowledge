from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from app.auth import require_current_user
from app.infrastructure.identity import IdentityUser
from app.infrastructure.topics import Topic, TopicError, TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


class CreateTopicRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=10_000)
    language: str = Field(default="zh-CN", min_length=2, max_length=32)
    query_profile: dict[str, Any] = Field(default_factory=dict)


class UpdateTopicRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=10_000)
    language: str | None = Field(default=None, min_length=2, max_length=32)
    query_profile: dict[str, Any] | None = None


def _service(request: Request) -> TopicService:
    engine: Engine = request.app.state.database_engine
    return TopicService(engine)


def _response(topic: Topic) -> dict[str, object]:
    return {
        "id": topic.id,
        "name": topic.name,
        "description": topic.description,
        "language": topic.language,
        "query_profile": topic.query_profile,
        "version": topic.version,
        "created_at": topic.created_at.isoformat(),
        "updated_at": topic.updated_at.isoformat(),
        "archived_at": topic.archived_at.isoformat()
        if topic.archived_at is not None
        else None,
    }


def _raise_error(error: TopicError) -> None:
    status_by_code = {
        "topic_name_taken": status.HTTP_409_CONFLICT,
        "topic_version_conflict": status.HTTP_412_PRECONDITION_FAILED,
        "topic_already_archived": status.HTTP_409_CONFLICT,
        "topic_name_required": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "topic_not_found": status.HTTP_404_NOT_FOUND,
    }
    raise HTTPException(
        status_code=status_by_code.get(error.code, status.HTTP_400_BAD_REQUEST),
        detail=error.code,
    )


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


@router.post("", status_code=status.HTTP_201_CREATED)
def create_topic(
    payload: CreateTopicRequest,
    service: Annotated[TopicService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
) -> dict[str, object]:
    result = service.create(
        user_id=user.id,
        name=payload.name,
        description=payload.description,
        language=payload.language,
        query_profile=payload.query_profile,
    )
    if isinstance(result, TopicError):
        _raise_error(result)
    return {"topic": _response(result)}


@router.get("")
def list_topics(
    service: Annotated[TopicService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    include_archived: Annotated[bool, Query()] = False,
) -> dict[str, object]:
    result = service.list_for_user(
        user_id=user.id,
        include_archived=include_archived,
    )
    return {"topics": [_response(topic) for topic in result]}


@router.get("/{topic_id}")
def get_topic(
    topic_id: str,
    service: Annotated[TopicService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
) -> dict[str, object]:
    result = service.get(user_id=user.id, topic_id=topic_id)
    if result is None:
        _raise_error(TopicError("topic_not_found"))
    return {"topic": _response(result)}


@router.patch("/{topic_id}")
def update_topic(
    topic_id: str,
    payload: UpdateTopicRequest,
    service: Annotated[TopicService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, object]:
    changes = payload.model_dump(exclude_unset=True)
    result = service.update(
        user_id=user.id,
        topic_id=topic_id,
        expected_version=_expected_version(if_match),
        changes=changes,
    )
    if isinstance(result, TopicError):
        _raise_error(result)
    return {"topic": _response(result)}


@router.post("/{topic_id}/archive")
def archive_topic(
    topic_id: str,
    service: Annotated[TopicService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, object]:
    result = service.archive(
        user_id=user.id,
        topic_id=topic_id,
        expected_version=_expected_version(if_match),
    )
    if isinstance(result, TopicError):
        _raise_error(result)
    return {"topic": _response(result)}