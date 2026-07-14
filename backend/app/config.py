from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = Field(
        default="mysql+pymysql://knowledge:knowledge_dev_password@mysql:3306/knowledge"
    )
    storage_notes_path: Path = Path("/app/data/notes")
    storage_uploads_path: Path = Path("/app/data/uploads")
    storage_raw_path: Path = Path("/app/data/raw")
    storage_parsed_path: Path = Path("/app/data/parsed")
    storage_exports_path: Path = Path("/app/data/exports")
    storage_cache_path: Path = Path("/app/data/cache")
    milvus_health_url: str = "http://milvus:9091/healthz"
    milvus_health_timeout_seconds: float = 2.0
    worker_idle_seconds: float = 5.0
    session_cookie_name: str = "knowledge_session"
    session_ttl_seconds: int = 43_200
    frontend_origin: str = "http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="KNOWLEDGE_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
