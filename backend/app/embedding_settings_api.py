from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from app.auth import require_current_user
from app.embedding import EmbeddingProviderSpec
from app.infrastructure.embedding_settings import EmbeddingSettingsService
from app.infrastructure.identity import IdentityUser

router = APIRouter(prefix="/embedding-settings", tags=["embedding-settings"])


class PutEmbeddingSettingsRequest(BaseModel):
    enabled: bool
    provider: str = Field(min_length=1, max_length=64)
    credential_name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    model_identifier: str = Field(min_length=1, max_length=512)
    dimension: int = Field(ge=1, le=65_536)
    response_format: str
    normalization: str = Field(default="l2", min_length=1, max_length=32)
    distance_metric: str = Field(default="cosine", min_length=1, max_length=32)


def _service(request: Request) -> EmbeddingSettingsService:
    engine: Engine = request.app.state.database_engine
    return EmbeddingSettingsService(engine)


@router.put("")
def put_settings(
    payload: PutEmbeddingSettingsRequest,
    service: Annotated[EmbeddingSettingsService, Depends(_service)],
    user: Annotated[IdentityUser, Depends(require_current_user)],
) -> dict[str, object]:
    settings = service.put(
        user_id=user.id,
        enabled=payload.enabled,
        spec=EmbeddingProviderSpec(
            provider=payload.provider,
            credential_name=payload.credential_name,
            base_url=payload.base_url,
            model=payload.model,
            model_identifier=payload.model_identifier,
            dimension=payload.dimension,
            normalization=payload.normalization,
            distance_metric=payload.distance_metric,
            response_format=payload.response_format,
        ),
    )
    return {
        "settings": {
            "enabled": settings.enabled,
            "provider": settings.spec.provider,
            "credential_name": settings.spec.credential_name,
            "base_url": settings.spec.base_url,
            "model": settings.spec.model,
            "model_identifier": settings.spec.model_identifier,
            "dimension": settings.spec.dimension,
            "response_format": settings.spec.response_format,
            "normalization": settings.spec.normalization,
            "distance_metric": settings.spec.distance_metric,
            "version": settings.version,
        }
    }