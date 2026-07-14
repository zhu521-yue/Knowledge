from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from app.infrastructure.identity import (
    IdentityError,
    IdentityService,
    IdentityUser,
    InvitationCode,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class BootstrapAdminRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=128)


class CreateInvitationRequest(BaseModel):
    code: str | None = Field(default=None, min_length=3, max_length=128)
    max_uses: int = Field(default=1, ge=1, le=100)
    expires_at: datetime | None = None


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=128)
    invitation_code: str = Field(min_length=1, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class SetUserStatusRequest(BaseModel):
    is_active: bool


def _identity_service(request: Request) -> IdentityService:
    engine: Engine = request.app.state.database_engine
    return IdentityService(engine)


def _user_response(user: IdentityUser) -> dict[str, object]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }


def _invitation_response(invitation: InvitationCode) -> dict[str, object]:
    return {
        "id": invitation.id,
        "code": invitation.code,
        "created_by_user_id": invitation.created_by_user_id,
        "max_uses": invitation.max_uses,
        "uses_count": invitation.uses_count,
        "is_active": invitation.is_active,
        "expires_at": invitation.expires_at.isoformat()
        if invitation.expires_at is not None
        else None,
        "created_at": invitation.created_at.isoformat(),
        "updated_at": invitation.updated_at.isoformat(),
    }


def _raise_identity_error(result: IdentityError) -> None:
    status_by_error = {
        "admin_already_initialized": status.HTTP_409_CONFLICT,
        "email_already_registered": status.HTTP_409_CONFLICT,
        "invitation_code_taken": status.HTTP_409_CONFLICT,
        "invitation_exhausted": status.HTTP_409_CONFLICT,
        "invalid_invitation_limit": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "admin_required": status.HTTP_403_FORBIDDEN,
        "invitation_disabled": status.HTTP_403_FORBIDDEN,
        "user_disabled": status.HTTP_403_FORBIDDEN,
        "invitation_not_found": status.HTTP_404_NOT_FOUND,
        "user_not_found": status.HTTP_404_NOT_FOUND,
        "invitation_expired": status.HTTP_410_GONE,
        "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
    }
    raise HTTPException(
        status_code=status_by_error.get(result.error, status.HTTP_400_BAD_REQUEST),
        detail=result.error,
    )


@router.post("/bootstrap-admin", status_code=status.HTTP_201_CREATED)
def bootstrap_admin(
    payload: BootstrapAdminRequest,
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> dict[str, object]:
    result = service.bootstrap_admin(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    if isinstance(result, IdentityError):
        _raise_identity_error(result)
    return {"user": _user_response(result)}


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
def create_invitation(
    payload: CreateInvitationRequest,
    service: Annotated[IdentityService, Depends(_identity_service)],
    actor_user_id: Annotated[str, Header(alias="X-Actor-User-Id")],
) -> dict[str, object]:
    result = service.create_invitation(
        actor_user_id=actor_user_id,
        code=payload.code,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
    )
    if isinstance(result, IdentityError):
        _raise_identity_error(result)
    return {"invitation": _invitation_response(result)}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> dict[str, object]:
    result = service.register_with_invitation(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        invitation_code=payload.invitation_code,
    )
    if isinstance(result, IdentityError):
        _raise_identity_error(result)
    return {"user": _user_response(result)}


@router.post("/login")
def login(
    payload: LoginRequest,
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> dict[str, object]:
    result = service.authenticate(email=payload.email, password=payload.password)
    if isinstance(result, IdentityError):
        _raise_identity_error(result)
    return {"user": _user_response(result)}


@router.patch("/users/{user_id}/status")
def set_user_status(
    user_id: str,
    payload: SetUserStatusRequest,
    service: Annotated[IdentityService, Depends(_identity_service)],
    actor_user_id: Annotated[str, Header(alias="X-Actor-User-Id")],
) -> dict[str, object]:
    result = service.set_user_active(
        actor_user_id=actor_user_id,
        target_user_id=user_id,
        is_active=payload.is_active,
    )
    if isinstance(result, IdentityError):
        _raise_identity_error(result)
    return {"user": _user_response(result)}