from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.identity_tables import identity_metadata
from app.main import create_app


def make_test_settings(tmp_path: Path, app_env: str = "development") -> Settings:
    return Settings(
        app_env=app_env,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'session.db'}",
        storage_notes_path=tmp_path / "notes",
        storage_uploads_path=tmp_path / "uploads",
        storage_raw_path=tmp_path / "raw",
        storage_parsed_path=tmp_path / "parsed",
        storage_exports_path=tmp_path / "exports",
        storage_cache_path=tmp_path / "cache",
        milvus_health_url="",
    )


def auth_client(tmp_path: Path, app_env: str = "development") -> Iterator[TestClient]:
    app = create_app(make_test_settings(tmp_path, app_env=app_env))
    with TestClient(app) as client:
        identity_metadata.create_all(app.state.database_engine)
        yield client


def create_user(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    admin = client.post(
        "/auth/bootstrap-admin",
        json={
            "email": "admin@example.test",
            "password": "correct horse battery staple",
            "display_name": "Admin",
        },
    ).json()["user"]
    invitation = client.post(
        "/auth/invitations",
        headers={"X-Actor-User-Id": admin["id"]},
        json={"code": "COOKIE", "max_uses": 1},
    ).json()["invitation"]
    member = client.post(
        "/auth/register",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
            "display_name": "Learner",
            "invitation_code": invitation["code"],
        },
    ).json()["user"]
    return admin, member


def test_login_sets_http_only_session_cookie_and_me_reads_current_user(tmp_path: Path) -> None:
    client = next(auth_client(tmp_path))
    _, member = create_user(client)

    login = client.post(
        "/auth/login",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
        },
    )
    me = client.get("/auth/me")

    assert login.status_code == 200
    assert "knowledge_session=" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=lax" in login.headers["set-cookie"]
    assert "Secure" not in login.headers["set-cookie"]
    assert me.status_code == 200
    assert me.json()["user"]["id"] == member["id"]


def test_production_login_cookie_uses_secure_flag(tmp_path: Path) -> None:
    client = next(auth_client(tmp_path, app_env="production"))
    create_user(client)

    login = client.post(
        "/auth/login",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
        },
    )

    assert login.status_code == 200
    assert "Secure" in login.headers["set-cookie"]


def test_refresh_extends_session_and_logout_clears_cookie(tmp_path: Path) -> None:
    client = next(auth_client(tmp_path))
    create_user(client)
    client.post(
        "/auth/login",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
        },
    )

    refreshed = client.post("/auth/session/refresh")
    logged_out = client.post("/auth/logout")
    me_after_logout = client.get("/auth/me")

    assert refreshed.status_code == 200
    assert refreshed.json()["user"]["email"] == "learner@example.test"
    assert logged_out.status_code == 204
    assert "knowledge_session=" in logged_out.headers["set-cookie"]
    assert "Max-Age=0" in logged_out.headers["set-cookie"]
    assert me_after_logout.status_code == 401


def test_disabled_account_invalidates_existing_cookie_session(tmp_path: Path) -> None:
    client = next(auth_client(tmp_path))
    admin, member = create_user(client)
    client.post(
        "/auth/login",
        json={
            "email": "learner@example.test",
            "password": "correct horse battery staple",
        },
    )

    disabled = client.patch(
        f"/auth/users/{member['id']}/status",
        headers={"X-Actor-User-Id": admin["id"]},
        json={"is_active": False},
    )
    me = client.get("/auth/me")

    assert disabled.status_code == 200
    assert me.status_code == 401
    assert me.json()["detail"] == "session_invalid"