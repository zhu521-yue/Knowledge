from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.identity_tables import identity_metadata
from app.main import create_app

MASTER_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def make_settings(tmp_path: Path, master_key: str | None = MASTER_KEY) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'credentials.db'}",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
        provider_credentials_master_key=master_key,
    )


def authenticate(client: TestClient) -> None:
    client.post(
        "/auth/bootstrap-admin",
        json={
            "email": "admin@example.test",
            "password": "correct horse battery staple",
            "display_name": "Admin",
        },
    )
    client.post(
        "/auth/login",
        json={
            "email": "admin@example.test",
            "password": "correct horse battery staple",
        },
    )


def test_provider_credentials_api_never_returns_plaintext(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))

    with TestClient(app) as client:
        identity_metadata.create_all(app.state.database_engine)
        authenticate(client)
        saved = client.put(
            "/provider-credentials/openai",
            json={"secret": "sk-api-secret-4321"},
        )
        listed = client.get("/provider-credentials")

    assert saved.status_code == 200
    assert saved.json()["credential"]["masked_secret"] == "**************4321"
    assert "sk-api-secret-4321" not in saved.text
    assert listed.status_code == 200
    assert "sk-api-secret-4321" not in listed.text
    assert listed.json()["credentials"][0]["provider"] == "openai"


def test_provider_credentials_require_authenticated_session(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/provider-credentials")

    assert response.status_code == 401


def test_missing_provider_master_key_rejects_app_startup(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, master_key=None))

    with pytest.raises(ValueError, match="KNOWLEDGE_PROVIDER_CREDENTIALS_MASTER_KEY"):
        with TestClient(app):
            pass