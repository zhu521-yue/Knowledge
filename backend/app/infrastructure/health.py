from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.infrastructure.database import check_database_connection
from app.infrastructure.milvus import MilvusUnavailable, check_milvus_health
from app.infrastructure.storage import StorageUnavailable, check_storage_paths

DependencyStatuses = dict[str, str]


@dataclass(frozen=True)
class DependenciesUnavailable(RuntimeError):
    statuses: DependencyStatuses


def inspect_dependencies(engine: Engine, settings: Settings) -> DependencyStatuses:
    statuses: DependencyStatuses = {}

    try:
        check_database_connection(engine)
        statuses["database"] = "ok"
    except SQLAlchemyError:
        statuses["database"] = "unavailable"

    try:
        check_storage_paths(settings)
        statuses["storage"] = "ok"
    except StorageUnavailable:
        statuses["storage"] = "unavailable"

    if not settings.milvus_health_url:
        statuses["milvus"] = "disabled"
    else:
        try:
            check_milvus_health(settings)
            statuses["milvus"] = "ok"
        except MilvusUnavailable:
            statuses["milvus"] = "unavailable"

    return statuses


def require_dependencies(engine: Engine, settings: Settings) -> DependencyStatuses:
    statuses = inspect_dependencies(engine, settings)
    if "unavailable" in statuses.values():
        raise DependenciesUnavailable(statuses)
    return statuses