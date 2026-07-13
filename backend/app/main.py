from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.infrastructure.database import (
    check_database_connection,
    create_database_engine,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    engine = create_database_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.database_engine = engine
        yield
        engine.dispose()

    app = FastAPI(title="Knowledge API", lifespan=lifespan)

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        try:
            check_database_connection(engine)
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="database_unavailable") from exc
        return {"status": "ok"}

    return app


app = create_app()
