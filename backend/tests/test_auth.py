import pytest
from fastapi.testclient import TestClient

from app.core.auth import set_auth_config
from app.core.db import SessionLocal
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _enable_auth(enabled: bool, password: str | None = None) -> None:
    with SessionLocal() as s:
        set_auth_config(s, enabled=enabled, password=password)


def _cleanup() -> None:
    from sqlalchemy import delete

    from app.models.entities import AppConfig

    with SessionLocal() as s:
        s.execute(delete(AppConfig).where(AppConfig.key == "auth"))
        s.commit()


@pytest.fixture(autouse=True)
def _auth_cleanup():
    _cleanup()
    yield
    _cleanup()


def test_default_no_auth_required(client):
    assert client.get("/api/health").status_code == 200
    # 默认 enabled=false，任意 API 免密
    assert client.get("/api/workspaces/1/documents").status_code in (200, 404)
    assert client.get("/api/documents/999999").status_code == 404


def test_enabled_requires_login(client):
    _enable_auth(True, "secret-pw")
    assert client.get("/api/health").status_code == 200  # 豁免
    assert client.get("/api/documents/1").status_code == 401


def test_login_success_and_access(client):
    _enable_auth(True, "secret-pw")
    resp = client.post("/api/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401
    resp = client.post("/api/auth/login", json={"password": "secret-pw"})
    assert resp.status_code == 204
    assert "rag_token" in resp.cookies
    assert resp.cookies["rag_token"]
    assert client.get("/api/documents/1").status_code in (200, 404)


def test_logout_clears_session(client):
    _enable_auth(True, "secret-pw")
    client.post("/api/auth/login", json={"password": "secret-pw"})
    assert client.get("/api/documents/1").status_code in (200, 404)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/documents/1").status_code == 401
