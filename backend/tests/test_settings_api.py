from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.core.security import decrypt_secret
from app.main import create_app
from app.models.entities import AppConfig, ProviderConfig, Workspace


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with SessionLocal() as s:
        s.execute(delete(ProviderConfig).where(ProviderConfig.base_url.like("http://mock")))
        s.execute(delete(AppConfig).where(AppConfig.key == "auth"))
        s.execute(delete(Workspace).where(Workspace.name.like("ws-settings-%")))
        s.commit()


def _mk_ws() -> Workspace:
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-settings-{uuid4()}")
        s.add(ws)
        s.commit()
        return ws


# ---------- provider CRUD ----------

def test_provider_create_mask_and_roundtrip(client):
    resp = client.post("/api/settings/providers", json={
        "kind": "llm", "base_url": "http://mock", "model": "m1",
        "api_key": "sk-secret", "is_default": True,
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["api_key"] == "***"
    assert "sk-secret" not in resp.text

    # 落库为密文
    with SessionLocal() as s:
        row = s.get(ProviderConfig, body["id"])
        assert row.api_key_encrypted and "sk-secret" not in row.api_key_encrypted
        assert decrypt_secret(row.api_key_encrypted) == "sk-secret"


def test_provider_list_and_no_plaintext(client):
    client.post("/api/settings/providers", json={
        "kind": "embedding", "base_url": "http://mock", "model": "e1",
        "api_key": "sk-emb",
    })
    resp = client.get("/api/settings/providers")
    assert resp.status_code == 200
    text = resp.text
    assert "sk-emb" not in text
    assert "api_key_encrypted" not in text
    target = [p for p in resp.json() if p["base_url"] == "http://mock"]
    assert target[0]["api_key"] == "***"

    # 无 key 的 provider 回显 null
    resp = client.post("/api/settings/providers", json={
        "kind": "llm", "base_url": "http://mock", "model": "m2",
    })
    assert resp.json()["api_key"] is None


def test_provider_update_keeps_key_when_blank(client):
    pid = client.post("/api/settings/providers", json={
        "kind": "llm", "base_url": "http://mock", "model": "m1", "api_key": "sk-old",
    }).json()["id"]
    resp = client.put(f"/api/settings/providers/{pid}", json={
        "kind": "llm", "base_url": "http://mock2", "model": "m1b",
    })
    assert resp.status_code == 200
    assert resp.json()["api_key"] == "***"
    with SessionLocal() as s:
        row = s.get(ProviderConfig, pid)
        assert decrypt_secret(row.api_key_encrypted) == "sk-old"
        assert row.base_url == "http://mock2"


def test_default_uniqueness_per_kind(client):
    a = client.post("/api/settings/providers", json={
        "kind": "llm", "base_url": "http://mock", "model": "a", "is_default": True,
    }).json()["id"]
    b = client.post("/api/settings/providers", json={
        "kind": "llm", "base_url": "http://mock", "model": "b", "is_default": True,
    }).json()["id"]
    # 同 kind 只有一个 default
    with SessionLocal() as s:
        assert not s.get(ProviderConfig, a).is_default
        assert s.get(ProviderConfig, b).is_default
    # 不同 kind 互不影响
    client.post("/api/settings/providers", json={
        "kind": "embedding", "base_url": "http://mock", "model": "e", "is_default": True,
    })
    with SessionLocal() as s:
        assert s.get(ProviderConfig, b).is_default
    # PUT 设回 a 为 default
    client.put(f"/api/settings/providers/{a}", json={
        "kind": "llm", "base_url": "http://mock", "model": "a", "is_default": True,
    })
    with SessionLocal() as s:
        assert s.get(ProviderConfig, a).is_default
        assert not s.get(ProviderConfig, b).is_default


def test_provider_delete_404_and_204(client):
    assert client.delete("/api/settings/providers/999999").status_code == 404
    pid = client.post("/api/settings/providers", json={
        "kind": "llm", "base_url": "http://mock", "model": "m",
    }).json()["id"]
    assert client.delete(f"/api/settings/providers/{pid}").status_code == 204
    assert client.get("/api/settings/providers").json() == [] or all(
        p["id"] != pid for p in client.get("/api/settings/providers").json()
    )


# ---------- 测试连接 ----------

def test_test_llm_ok(client, httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://mock/chat/completions",
        json={"choices": [{"message": {"content": "pong"}}]},
    )
    resp = client.post("/api/settings/providers/test", json={
        "kind": "llm", "base_url": "http://mock", "model": "m1", "api_key": "sk",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    req = httpx_mock.get_requests()[0]
    assert b'"ping"' in req.content and b'"max_tokens":8' in req.content.replace(b" ", b"")


def test_test_embedding_ok_dim(client, httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://mock/embeddings",
        json={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]},
    )
    resp = client.post("/api/settings/providers/test", json={
        "kind": "embedding", "base_url": "http://mock", "model": "e1",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "dim": 4}


def test_test_rerank_501(client):
    resp = client.post("/api/settings/providers/test", json={
        "kind": "rerank", "base_url": "http://mock", "model": "r1",
    })
    assert resp.status_code == 501
    assert resp.json()["detail"] == "rerank 测试将在 M3 支持"


def test_test_failure_400_truncated(client, httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://mock/chat/completions", status_code=500,
        text="x" * 1000,
    )
    resp = client.post("/api/settings/providers/test", json={
        "kind": "llm", "base_url": "http://mock", "model": "m1",
    })
    assert resp.status_code == 400
    assert len(resp.json()["detail"]) <= 500


def test_test_connection_error_400(client, httpx_mock):
    import httpx as _httpx

    httpx_mock.add_exception(_httpx.ConnectError("connection refused"))
    resp = client.post("/api/settings/providers/test", json={
        "kind": "llm", "base_url": "http://mock", "model": "m1",
    })
    assert resp.status_code == 400


# ---------- 模型发现 ----------

def test_discover_models_ok(client, httpx_mock):
    httpx_mock.add_response(
        method="GET", url="http://mock/models",
        json={"data": [{"id": "m-a"}, {"id": "m-b"}]},
    )
    resp = client.get("/api/settings/models", params={
        "base_url": "http://mock", "api_key": "sk-x",
    })
    assert resp.status_code == 200
    assert resp.json() == {"models": ["m-a", "m-b"]}
    assert httpx_mock.get_requests()[0].headers["authorization"] == "Bearer sk-x"


def test_discover_models_fail_400(client, httpx_mock):
    import httpx as _httpx

    httpx_mock.add_exception(_httpx.ConnectError("timeout"))
    resp = client.get("/api/settings/models", params={"base_url": "http://mock"})
    assert resp.status_code == 400


# ---------- 应用设置 ----------

def test_app_settings_default_and_toggle(client):
    assert client.get("/api/settings/app").json() == {"auth_enabled": False}
    # 开启必须带密码
    assert client.put("/api/settings/app", json={"auth_enabled": True}).status_code == 422
    resp = client.put("/api/settings/app", json={"auth_enabled": True, "password": "pw1"})
    assert resp.status_code == 200
    # 已开启认证，需登录后读取
    client.post("/api/auth/login", json={"password": "pw1"})
    assert client.get("/api/settings/app").json() == {"auth_enabled": True}
    # GET 不回显 hash
    assert "password" not in client.get("/api/settings/app").text
    # 关闭清除
    assert client.put("/api/settings/app", json={"auth_enabled": False}).status_code == 200
    assert client.get("/api/settings/app").json() == {"auth_enabled": False}


def test_app_enabled_blocks_without_login(client):
    client.put("/api/settings/app", json={"auth_enabled": True, "password": "pw1"})
    assert client.get("/api/settings/providers").status_code == 401
    client.post("/api/auth/login", json={"password": "pw1"})
    assert client.get("/api/settings/providers").status_code == 200


# ---------- workspace 检索参数 ----------

def test_workspace_settings_defaults_and_put(client):
    ws = _mk_ws()
    resp = client.get(f"/api/workspaces/{ws.id}/settings")
    assert resp.status_code == 200
    assert resp.json() == {
        "top_k": 5, "score_threshold": 0.0,
        "use_rerank": True, "context_max_tokens": 3000,
    }
    resp = client.put(f"/api/workspaces/{ws.id}/settings", json={
        "top_k": 10, "score_threshold": 0.5,
        "use_rerank": False, "context_max_tokens": 4000,
    })
    assert resp.status_code == 200
    assert client.get(f"/api/workspaces/{ws.id}/settings").json() == {
        "top_k": 10, "score_threshold": 0.5,
        "use_rerank": False, "context_max_tokens": 4000,
    }


def test_workspace_settings_range_validation(client):
    ws = _mk_ws()
    for bad in [
        {"top_k": 0, "score_threshold": 0.0, "use_rerank": True, "context_max_tokens": 3000},
        {"top_k": 21, "score_threshold": 0.0, "use_rerank": True, "context_max_tokens": 3000},
        {"top_k": 5, "score_threshold": 1.5, "use_rerank": True, "context_max_tokens": 3000},
        {"top_k": 5, "score_threshold": 0.0, "use_rerank": True, "context_max_tokens": 100},
        {"top_k": 5, "score_threshold": 0.0, "use_rerank": True, "context_max_tokens": 9000},
    ]:
        assert client.put(f"/api/workspaces/{ws.id}/settings", json=bad).status_code == 422
    assert client.get(f"/api/workspaces/{ws.id}/settings").status_code == 200


def test_workspace_settings_404(client):
    assert client.get("/api/workspaces/999999/settings").status_code == 404
    assert client.put("/api/workspaces/999999/settings", json={
        "top_k": 5, "score_threshold": 0.0,
        "use_rerank": True, "context_max_tokens": 3000,
    }).status_code == 404
