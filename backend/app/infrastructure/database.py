from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    active_settings = settings or get_settings()
    return create_engine(
        active_settings.database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def check_database_connection(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
