from __future__ import annotations

import argparse
import logging
import signal
import threading

from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.infrastructure.database import create_database_engine
from app.infrastructure.health import require_dependencies
from app.observability import configure_structured_logging

logger = logging.getLogger(__name__)


def check_worker_dependencies(settings: Settings | None = None) -> dict[str, str]:
    configure_structured_logging()
    active_settings = settings or get_settings()
    engine = create_database_engine(active_settings)
    try:
        dependencies = require_dependencies(engine, active_settings)
        logger.info(
            "worker_health_check",
            extra={"service": "worker", "dependencies": dependencies},
        )
        return dependencies
    finally:
        engine.dispose()


def run_worker(
    settings: Settings | None = None,
    stop_event: threading.Event | None = None,
    engine: Engine | None = None,
) -> None:
    configure_structured_logging()
    active_settings = settings or get_settings()
    active_stop_event = stop_event or threading.Event()
    active_engine = engine or create_database_engine(active_settings)

    try:
        dependencies = require_dependencies(active_engine, active_settings)
        logger.info(
            "worker_ready",
            extra={"service": "worker", "dependencies": dependencies},
        )

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
