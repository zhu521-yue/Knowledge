from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

MASTER_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def valid_values(tmp_path: Path) -> dict[str, object]:
    return {
        "database_url": "sqlite+pysqlite:///:memory:",
        "storage_notes_path": tmp_path / "notes",
        "storage_uploads_path": tmp_path / "uploads",
        "storage_raw_path": tmp_path / "raw",
        "storage_parsed_path": tmp_path / "parsed",
        "storage_exports_path": tmp_path / "exports",
        "storage_cache_path": tmp_path / "cache",
        "milvus_health_url": "",
        "provider_credentials_master_key": MASTER_KEY,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_url", ""),
        ("session_ttl_seconds", 0),
        ("worker_idle_seconds", 0),
        ("milvus_health_timeout_seconds", 0),
        ("frontend_origin", "not-a-url"),
    ],
)
def test_invalid_startup_config_identifies_field(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    values = valid_values(tmp_path)
    values[field] = value

    with pytest.raises(ValidationError) as exc_info:
        Settings(**values)

    assert field in str(exc_info.value)