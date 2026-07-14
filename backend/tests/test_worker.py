import threading
from pathlib import Path

from sqlalchemy import create_engine

from app.config import Settings
from app.worker import check_worker_dependencies, run_worker


def make_worker_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
        worker_idle_seconds=0.01,
        provider_credentials_master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def test_worker_dependency_check(tmp_path: Path) -> None:
    check_worker_dependencies(make_worker_settings(tmp_path))


def test_worker_loop_stops_cleanly(tmp_path: Path) -> None:
    settings = make_worker_settings(tmp_path)
    stop_event = threading.Event()
    stop_event.set()
    engine = create_engine(settings.database_url)

    run_worker(settings=settings, stop_event=stop_event, engine=engine)
