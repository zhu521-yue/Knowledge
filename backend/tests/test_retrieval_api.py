from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.identity_tables import identity_metadata
from app.retrieval import RankedChildEvidence, RetrievalResult, RetrievedParent
from app.retrieval_api import _result_response
from app.retrieval_service import RetrievalUseCaseError
from app.main import create_app


class _UseCase:
    def __init__(self, result: RetrievalResult | None = None) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    def execute(self, *, user_id: str, topic_id: str, query: str) -> RetrievalResult:
        self.calls.append((user_id, topic_id, query))
        if self.result is None:
            raise RetrievalUseCaseError("topic_not_found")
        return self.result


def _result() -> RetrievalResult:
    evidence = RankedChildEvidence(
        child_chunk_id="child-1",
        text="命中子块",
        page_start=2,
        page_end=2,
        parent_char_start=10,
        parent_char_end=20,
        dense_rank=1,
        sparse_rank=2,
        rrf_score=0.03,
    )
    parent = RetrievedParent(
        parent_chunk_id="parent-1",
        source_document_id="source-1",
        source_revision_id="revision-1",
        ingestion_run_id="run-1",
        heading_path=("检索",),
        page_start=1,
        page_end=2,
        text="完整父块正文",
        score=0.03,
        evidence=(evidence,),
    )
    return RetrievalResult(
        retrieval_version="retrieval-v1",
        dense_index_version="dense-v1",
        sparse_index_version="bm25-v1",
        topic_id="topic-1",
        active_run_ids=("run-1",),
        parents=(parent,),
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'retrieval.db'}",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
        provider_credentials_master_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )


def _login(client: TestClient) -> str:
    email = "admin@example.test"
    password = "correct horse battery staple"
    client.post(
        "/auth/bootstrap-admin",
        json={"email": email, "password": password, "display_name": "Admin"},
    )
    response = client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["user"]["id"]


def test_response_contains_parent_source_and_child_rank_evidence() -> None:
    response = _result_response(_result())

    assert response["retrieval_version"] == "retrieval-v1"
    assert response["active_run_ids"] == ["run-1"]
    parent = response["parents"][0]
    assert parent["source_document_id"] == "source-1"
    assert parent["page_start"] == 1
    assert parent["evidence"][0]["dense_rank"] == 1
    assert parent["evidence"][0]["sparse_rank"] == 2


def test_retrieval_api_requires_session_and_uses_authenticated_user(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    use_case = _UseCase(_result())
    with TestClient(app) as client:
        identity_metadata.create_all(app.state.database_engine)
        app.state.retrieval_use_case = use_case
        unauthorized = client.post(
            "/retrieval", json={"topic_id": "topic-1", "query": "什么是 RRF"}
        )
        user_id = _login(client)
        response = client.post(
            "/retrieval", json={"topic_id": "topic-1", "query": "什么是 RRF"}
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["retrieval"]["parents"][0]["text"] == "完整父块正文"
    assert use_case.calls == [(user_id, "topic-1", "什么是 RRF")]


def test_retrieval_api_hides_foreign_or_missing_topic(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        identity_metadata.create_all(app.state.database_engine)
        app.state.retrieval_use_case = _UseCase()
        _login(client)
        response = client.post(
            "/retrieval", json={"topic_id": "foreign-topic", "query": "query"}
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "topic_not_found"