from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import router as auth_router
from app.config import Settings, get_settings
from app.ingestion import router as ingestion_router
from app.infrastructure.database import create_database_engine
from app.infrastructure.health import inspect_dependencies
from app.infrastructure.provider_credentials import ProviderCredentialService
from app.observability import RequestContextMiddleware, configure_structured_logging
from app.provider_credentials import router as provider_credentials_router
from app.topics import router as topics_router


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_structured_logging()
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
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[active_settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(auth_router)
    app.include_router(ingestion_router)
    app.include_router(provider_credentials_router)
    app.include_router(topics_router)

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> JSONResponse:
        dependencies = inspect_dependencies(engine, active_settings)
        payload = {
            "status": "ok" if "unavailable" not in dependencies.values() else "degraded",
            "service": "api",
            "dependencies": dependencies,
        }
        status_code = 200 if payload["status"] == "ok" else 503
        return JSONResponse(status_code=status_code, content=payload)

    return app


app = create_app()
