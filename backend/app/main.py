from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.auth import router as auth_router
from app.config import Settings, get_settings
from app.infrastructure.database import (
    check_database_connection,
    create_database_engine,
)
from app.infrastructure.milvus import MilvusUnavailable, check_milvus_health
from app.infrastructure.provider_credentials import ProviderCredentialService
from app.infrastructure.storage import StorageUnavailable, check_storage_paths
from app.provider_credentials import router as provider_credentials_router


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    engine = create_database_engine(active_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        master_key = active_settings.provider_credentials_master_key
        if master_key is None:
            raise ValueError(
                "KNOWLEDGE_PROVIDER_CREDENTIALS_MASTER_KEY is required"
            )
        app.state.provider_credential_service = ProviderCredentialService(
            engine,
            master_key.get_secret_value(),
        )
        app.state.database_engine = engine
        yield
        engine.dispose()

    app = FastAPI(title="Knowledge API", lifespan=lifespan)
    app.state.settings = active_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[active_settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(provider_credentials_router)

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        try:
            check_database_connection(engine)
            check_storage_paths(active_settings)
            check_milvus_health(active_settings)
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="database_unavailable") from exc
        except StorageUnavailable as exc:
            raise HTTPException(status_code=503, detail="storage_unavailable") from exc
        except MilvusUnavailable as exc:
            raise HTTPException(status_code=503, detail="milvus_unavailable") from exc
        return {"status": "ok"}

    return app


app = create_app()
