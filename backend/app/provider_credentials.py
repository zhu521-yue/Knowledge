from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from app.auth import require_current_user
from app.infrastructure.identity import IdentityUser
from app.infrastructure.provider_credentials import (
    ProviderCredential,
    ProviderCredentialService,
)

router = APIRouter(prefix="/provider-credentials", tags=["provider-credentials"])


class StoreProviderCredentialRequest(BaseModel):
    secret: str = Field(min_length=1, max_length=8192)


def _credential_service(request: Request) -> ProviderCredentialService:
    return request.app.state.provider_credential_service


def _credential_response(credential: ProviderCredential) -> dict[str, object]:
    return {
        "id": credential.id,
        "provider": credential.provider,
        "masked_secret": credential.masked_secret,
        "version": credential.version,
        "created_at": credential.created_at.isoformat(),
        "updated_at": credential.updated_at.isoformat(),
    }


@router.get("")
def list_provider_credentials(
    user: Annotated[IdentityUser, Depends(require_current_user)],
    service: Annotated[ProviderCredentialService, Depends(_credential_service)],
) -> dict[str, object]:
    return {
        "credentials": [
            _credential_response(credential)
            for credential in service.list_masked(user_id=user.id)
        ]
    }


@router.put("/{provider}")
def store_provider_credential(
    provider: str,
    payload: StoreProviderCredentialRequest,
    user: Annotated[IdentityUser, Depends(require_current_user)],
    service: Annotated[ProviderCredentialService, Depends(_credential_service)],
) -> dict[str, object]:
    credential = service.store(
        user_id=user.id,
        provider=provider,
        secret=payload.secret,
    )
    return {"credential": _credential_response(credential)}