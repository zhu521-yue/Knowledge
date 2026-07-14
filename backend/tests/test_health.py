from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.storage import iter_required_storage_paths
from app.main import create_app


def make_test_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
    )


def test_liveness_endpoint(tmp_path: Path) -> None:
    response = TestClient(create_app(make_test_settings(tmp_path))).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_endpoint_checks_dependencies(tmp_path: Path) -> None:
    settings = make_test_settings(tmp_path)

    response = TestClient(create_app(settings)).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert all(path.exists() for path in iter_required_storage_paths(settings))
