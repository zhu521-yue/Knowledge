from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = Field(
        default="mysql+pymysql://knowledge:knowledge_dev_password@mysql:3306/knowledge"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="KNOWLEDGE_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
