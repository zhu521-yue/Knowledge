from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import router as auth_router
from app.config import Settings, get_settings
from app.dense_index import DenseIndexService
from app.embedding_settings_api import router as embedding_settings_router
from app.ingestion import router as ingestion_router
from app.infrastructure.chunk_store import ChunkStore
from app.infrastructure.database import create_database_engine
from app.infrastructure.dense_milvus import MilvusDenseIndex
from app.infrastructure.embedding_query import AuthorizedQueryEmbedding
from app.infrastructure.embedding_settings import EmbeddingSettingsService
from app.infrastructure.health import inspect_dependencies
from app.infrastructure.provider_credentials import ProviderCredentialService
from app.infrastructure.retrieval_artifacts import FileRetrievalArtifacts
from app.infrastructure.retrieval_scope import RetrievalScopeResolver
from app.infrastructure.source_imports import LocalSourceImportService, WebSourceImportService
from app.infrastructure.sparse_index_store import SparseIndexStore
from app.infrastructure.web_fetch import SafeWebFetcher
from app.observability import RequestContextMiddleware, configure_structured_logging
from app.provider_credentials import router as provider_credentials_router
from app.retrieval_api import router as retrieval_router
from app.retrieval_service import RetrieveTopicParents
from app.sources import router as sources_router
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
        credential_service = ProviderCredentialService(
            engine,
            master_key.get_secret_value(),
        )
        app.state.provider_credential_service = credential_service
        app.state.web_source_import_service = WebSourceImportService(
            engine,
            active_settings.storage_raw_path,
            SafeWebFetcher(),
        )
        app.state.local_source_import_service = LocalSourceImportService(
            engine,
            active_settings.storage_raw_path,
        )
        embedding_settings = EmbeddingSettingsService(engine)
        app.state.retrieval_use_case = RetrieveTopicParents(
            scope_resolver=RetrievalScopeResolver(engine),
            query_embedding=AuthorizedQueryEmbedding(
                embedding_settings,
                credential_service,
            ),
            dense_index=DenseIndexService(MilvusDenseIndex(active_settings.milvus_uri)),
            artifacts=FileRetrievalArtifacts(
                chunks=ChunkStore(active_settings.storage_parsed_path),
                sparse_indexes=SparseIndexStore(
                    active_settings.storage_cache_path / "sparse"
                ),
            ),
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
    app.include_router(embedding_settings_router)
    app.include_router(ingestion_router)
    app.include_router(provider_credentials_router)
    app.include_router(retrieval_router)
    app.include_router(sources_router)
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
