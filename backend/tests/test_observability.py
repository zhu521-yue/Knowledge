import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.observability import JsonFormatter, redact

MASTER_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
        provider_credentials_master_key=MASTER_KEY,
    )


def test_request_id_is_propagated_and_generated(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        propagated = client.get("/health/live", headers={"X-Request-ID": "trace-123"})
        generated = client.get("/health/live")

    assert propagated.headers["X-Request-ID"] == "trace-123"
    assert generated.headers["X-Request-ID"]
    assert generated.headers["X-Request-ID"] != "trace-123"


def test_request_id_is_visible_to_browser_clients(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get(
            "/health/ready",
            headers={"Origin": "http://127.0.0.1:3000"},
        )

    exposed_headers = response.headers.get("Access-Control-Expose-Headers", "")
    assert "x-request-id" in exposed_headers.lower()


def test_json_formatter_is_structured_and_redacts_sensitive_fields() -> None:
    record = logging.LogRecord(
        name="knowledge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provider_saved",
        args=(),
        exc_info=None,
    )
    record.request_id = "trace-456"
    record.details = redact(
        {
            "provider": "openai",
            "api_key": "sk-plain-secret",
            "nested": {"password": "plain-password", "status": "ok"},
        }
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "provider_saved"
    assert payload["request_id"] == "trace-456"
    assert payload["details"]["api_key"] == "[REDACTED]"
    assert payload["details"]["nested"]["password"] == "[REDACTED]"
    assert "sk-plain-secret" not in json.dumps(payload)
    assert "plain-password" not in json.dumps(payload)


def test_request_log_excludes_query_and_body_secrets(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="knowledge.request")

    with TestClient(create_app(make_settings(tmp_path))) as client:
        client.post("/auth/login?api_key=query-secret", json={"password": "body-secret"})

    request_records = [record for record in caplog.records if record.name == "knowledge.request"]
    assert request_records
    serialized = " ".join(record.getMessage() + repr(record.__dict__) for record in request_records)
    assert "query-secret" not in serialized
    assert "body-secret" not in serialized