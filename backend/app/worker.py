from __future__ import annotations

import argparse
import logging
import signal
import socket
import threading
from uuid import uuid4

from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.dense_index import DenseIndexService
from app.infrastructure.background_processing import IngestionProcessor, SourcePurgeProcessor
from app.infrastructure.chunk_store import ChunkStore
from app.infrastructure.database import create_database_engine
from app.infrastructure.dense_milvus import MilvusDenseIndex
from app.infrastructure.embedding_settings import EmbeddingSettingsService
from app.infrastructure.health import require_dependencies
from app.infrastructure.parsed_document_store import ParsedDocumentStore
from app.infrastructure.provider_credentials import ProviderCredentialService
from app.infrastructure.sparse_index_store import SparseIndexStore
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

        master_key = active_settings.provider_credentials_master_key
        if master_key is None:
            raise ValueError("KNOWLEDGE_PROVIDER_CREDENTIALS_MASTER_KEY is required")
        worker_id = f"{socket.gethostname()}-{uuid4().hex[:12]}"
        dense_index = DenseIndexService(MilvusDenseIndex(active_settings.milvus_uri))
        sparse_store = SparseIndexStore(active_settings.storage_cache_path / "sparse")
        ingestion = IngestionProcessor(
            engine=active_engine,
            parsed_store=ParsedDocumentStore(active_settings.storage_parsed_path),
            chunk_store=ChunkStore(active_settings.storage_parsed_path),
            sparse_store=sparse_store,
            dense_index=dense_index,
            embedding_settings=EmbeddingSettingsService(active_engine),
            credentials=ProviderCredentialService(
                active_engine,
                master_key.get_secret_value(),
            ),
        )
        purges = SourcePurgeProcessor(
            engine=active_engine,
            parsed_root=active_settings.storage_parsed_path,
            sparse_store=sparse_store,
            dense_index=dense_index,
            worker_id=worker_id,
        )
        while not active_stop_event.is_set():
            processed = ingestion.process_next() or purges.process_next()
            if not processed:
                active_stop_event.wait(active_settings.worker_idle_seconds)
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
