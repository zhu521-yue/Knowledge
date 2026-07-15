from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from pymilvus import DataType, MilvusClient

from app.dense_index import DenseIndexRecord, DenseSearchHit
from app.retrieval_scope import AuthorizedRetrievalScope


class MilvusDenseIndex:
    def __init__(self, uri: str) -> None:
        self._client = MilvusClient(uri=uri)

    def ensure_collection(self, index_version: str, dimension: int) -> None:
        collection = _collection_name(index_version)
        if self._client.has_collection(collection_name=collection):
            description = self._client.describe_collection(collection_name=collection)
            vector_field = next(
                field for field in description["fields"] if field["name"] == "vector"
            )
            if int(vector_field["params"]["dim"]) != dimension:
                raise ValueError("dense collection dimension mismatch")
            return
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(
            field_name="child_chunk_id",
            datatype=DataType.VARCHAR,
            is_primary=True,
            max_length=64,
        )
        for field in (
            "parent_chunk_id",
            "user_id",
            "source_document_id",
            "source_revision_id",
            "ingestion_run_id",
            "index_version",
        ):
            schema.add_field(field_name=field, datatype=DataType.VARCHAR, max_length=128)
        for field in (
            "page_start",
            "page_end",
            "parent_char_start",
            "parent_char_end",
        ):
            schema.add_field(field_name=field, datatype=DataType.INT64)
        schema.add_field(
            field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension
        )
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        self._client.create_collection(
            collection_name=collection,
            schema=schema,
            index_params=index_params,
        )

    def upsert(
        self, index_version: str, records: Sequence[DenseIndexRecord]
    ) -> None:
        if not records:
            return
        self._client.upsert(
            collection_name=_collection_name(index_version),
            data=[_record_dict(record) for record in records],
        )

    def delete_runs(self, index_version: str, run_ids: frozenset[str]) -> None:
        if not run_ids:
            return
        self._client.delete(
            collection_name=_collection_name(index_version),
            filter=_filter_expression(None, run_ids),
        )

    def search(
        self,
        index_version: str,
        query_vector: Sequence[float],
        *,
        scope: AuthorizedRetrievalScope,
        limit: int,
    ) -> Sequence[DenseSearchHit]:
        output_fields = [
            "child_chunk_id",
            "parent_chunk_id",
            "ingestion_run_id",
            "page_start",
            "page_end",
            "parent_char_start",
            "parent_char_end",
        ]
        results = self._client.search(
            collection_name=_collection_name(index_version),
            data=[list(query_vector)],
            filter=_filter_expression(scope.user_id, scope.active_run_ids),
            limit=limit,
            output_fields=output_fields,
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
        )
        rows: list[dict[str, Any]] = results[0] if results else []
        return tuple(
            DenseSearchHit(
                child_chunk_id=str(row["entity"]["child_chunk_id"]),
                parent_chunk_id=str(row["entity"]["parent_chunk_id"]),
                ingestion_run_id=str(row["entity"]["ingestion_run_id"]),
                score=float(row["distance"]),
                rank=rank,
                page_start=int(row["entity"]["page_start"]),
                page_end=int(row["entity"]["page_end"]),
                parent_char_start=int(row["entity"]["parent_char_start"]),
                parent_char_end=int(row["entity"]["parent_char_end"]),
            )
            for rank, row in enumerate(rows, start=1)
        )


def _collection_name(index_version: str) -> str:
    digest = hashlib.sha256(index_version.encode()).hexdigest()[:24]
    return f"dense_{digest}"


def _filter_expression(user_id: str | None, run_ids: frozenset[str]) -> str:
    run_values = ",".join(json.dumps(run_id) for run_id in sorted(run_ids))
    run_filter = f"ingestion_run_id in [{run_values}]"
    if user_id is None:
        return run_filter
    return f"user_id == {json.dumps(user_id)} and {run_filter}"


def _record_dict(record: DenseIndexRecord) -> dict[str, object]:
    return {
        "child_chunk_id": record.child_chunk_id,
        "parent_chunk_id": record.parent_chunk_id,
        "user_id": record.user_id,
        "source_document_id": record.source_document_id,
        "source_revision_id": record.source_revision_id,
        "ingestion_run_id": record.ingestion_run_id,
        "index_version": record.index_version,
        "page_start": record.page_start,
        "page_end": record.page_end,
        "parent_char_start": record.parent_char_start,
        "parent_char_end": record.parent_char_end,
        "vector": list(record.vector),
    }