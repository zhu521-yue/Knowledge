from __future__ import annotations

import argparse
import logging
import signal
import threading

from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.infrastructure.database import (
    check_database_connection,
    create_database_engine,
)
from app.infrastructure.milvus import check_milvus_health
from app.infrastructure.storage import check_storage_paths

logger = logging.getLogger(__name__)


def check_worker_dependencies(settings: Settings | None = None) -> None:
    active_settings = settings or get_settings()
    engine = create_database_engine(active_settings)
    try:
        check_database_connection(engine)
        check_storage_paths(active_settings)
        check_milvus_health(active_settings)
    finally:
        engine.dispose()


def run_worker(
    settings: Settings | None = None,
    stop_event: threading.Event | None = None,
    engine: Engine | None = None,
) -> None:
    logging.basicConfig(level=logging.INFO)
    active_settings = settings or get_settings()
    active_stop_event = stop_event or threading.Event()
    active_engine = engine or create_database_engine(active_settings)

    try:
        check_database_connection(active_engine)
        check_storage_paths(active_settings)
        check_milvus_health(active_settings)
        logger.info("worker_ready")

        while not active_stop_event.wait(active_settings.worker_idle_seconds):
            logger.debug("worker_idle")
    finally:
        active_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge worker process")
    parser.add_argument(
        "--check", action="store_true", help="check dependencies and exit"
    )
    args = parser.parse_args()

    if args.check:
        check_worker_dependencies()
        return

    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run_worker(stop_event=stop_event)


if __name__ == "__main__":
    main()
