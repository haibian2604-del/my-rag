"""API Key 端到端测试（M7-T1）：创建明文一次性 / 列表无敏感信息 / 吊销后失效 / 404 / 401。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.main import create_app
from app.models.entities import ApiKey


@pytest.fixture
def raw_client() -> TestClient:
    """未登录的裸客户端（不经过 conftest 的已登录 client）。"""
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clean_api_keys():
    """每条测试前清空 api_keys 表（测试直连共享开发库，前置清空兜底）。"""
    with SessionLocal() as s:
        s.execute(delete(ApiKey))
        s.commit()
    yield
    with SessionLocal() as s:
        s.execute(delete(ApiKey))
        s.commit()


def test_create_returns_plaintext_once(client):
    resp = client.post("/api/keys", json={"name": "claude-code"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "claude-code"
    assert data["key"].startswith("zk-")
    assert data["key_prefix"] == data["key"][:8]
    # 库里只有 bcrypt 哈希，无明文
    with SessionLocal() as s:
        row = s.get(ApiKey, data["id"])
        assert row is not None
        assert row.key_hash.startswith("$2")
        assert data["key"] not in row.key_hash
        assert row.last_used_at is None


def test_list_has_no_secret(client):
    created = client.post("/api/keys", json={"name": "k1"}).json()
    resp = client.get("/api/keys")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["id"] == created["id"]
    assert item["key_prefix"] == created["key_prefix"]
    assert set(item) == {"id", "name", "key_prefix", "created_at", "last_used_at"}
    # 列表不含明文与哈希
    assert "key" not in item and "key_hash" not in item
    assert created["key"] not in resp.text


def test_verify_fails_after_revoke(client):
    created = client.post("/api/keys", json={"name": "k1"}).json()
    from app.services import api_keys_service

    assert api_keys_service.verify(created["key"]) is not None
    resp = client.delete(f"/api/keys/{created['id']}")
    assert resp.status_code == 204
    assert api_keys_service.verify(created["key"]) is None


def test_delete_missing_returns_404(client):
    resp = client.delete("/api/keys/99999")
    assert resp.status_code == 404


def test_unauthenticated_401(raw_client):
    assert raw_client.post("/api/keys", json={"name": "x"}).status_code == 401
    assert raw_client.get("/api/keys").status_code == 401
    assert raw_client.delete("/api/keys/1").status_code == 401


def test_touch_updates_last_used(client):
    created = client.post("/api/keys", json={"name": "k1"}).json()
    from app.services import api_keys_service

    api_keys_service.touch(created["id"])
    with SessionLocal() as s:
        row = s.get(ApiKey, created["id"])
        assert row.last_used_at is not None
