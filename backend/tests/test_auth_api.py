from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.identity_tables import identity_metadata
from app.main import create_app


def make_test_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'identity.db'}",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
    )


def auth_client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(make_test_settings(tmp_path))
    with TestClient(app) as client:
        identity_metadata.create_all(app.state.database_engine)
        yield client


def test_auth_flow_bootstrap_invite_register_login_and_disable(tmp_path: Path) -> None:
    client = next(auth_client(tmp_path))

    bootstrap = client.post(
        "/auth/bootstrap-admin",
        json={
            "email": "admin@example.test",
            "password": "correct horse battery staple",
            "display_name": "Admin",
        },
    )
    admin = bootstrap.json()["user"]

    duplicate_bootstrap = client.post(
        "/auth/bootstrap-admin",
        json={
            "email": "other@example.test",
            "password": "correct horse battery staple",
            "display_name": "Other",
        },
    )
    invitation = client.post(
        "/auth/invitations",
        headers={"X-Actor-User-Id": admin["id"]},
        json={"code": "FRONTEND-VERIFY", "max_uses": 1},
    )
    registered = client.post(
        "/auth/register",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
            "display_name": "Learner",
            "invitation_code": invitation.json()["invitation"]["code"],
        },
    )
    login = client.post(
        "/auth/login",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
        },
    )
    disabled = client.patch(
        f"/auth/users/{registered.json()['user']['id']}/status",
        headers={"X-Actor-User-Id": admin["id"]},
        json={"is_active": False},
    )
    disabled_login = client.post(
        "/auth/login",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
        },
    )

    assert bootstrap.status_code == 201
    assert admin["role"] == "admin"
    assert duplicate_bootstrap.status_code == 409
    assert invitation.status_code == 201
    assert registered.status_code == 201
    assert registered.json()["user"]["role"] == "member"
    assert login.status_code == 200
    assert disabled.status_code == 200
    assert disabled.json()["user"]["is_active"] is False
    assert disabled_login.status_code == 403
    assert disabled_login.json()["detail"] == "user_disabled"


def test_register_rejects_invalid_invitation(tmp_path: Path) -> None:
    client = next(auth_client(tmp_path))

    response = client.post(
        "/auth/register",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
            "display_name": "Learner",
            "invitation_code": "MISSING",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "invitation_not_found"