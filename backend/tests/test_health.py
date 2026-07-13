from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_liveness_endpoint() -> None:
    response = TestClient(create_app()).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_endpoint_checks_database() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")

    response = TestClient(create_app(settings)).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
