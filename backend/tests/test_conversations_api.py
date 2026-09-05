"""会话列表 API 测试：列表倒序 / PUT 改标题 / 校验 / 跨 workspace 隔离。"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.models.entities import Conversation, Workspace


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture
def ws_id() -> int:
    with SessionLocal() as s:
        ws = Workspace(name=f"conv-api-{uuid4().hex[:8]}")
        s.add(ws)
        s.commit()
        wid = ws.id
    yield wid
    with SessionLocal() as s:
        ws = s.get(Workspace, wid)
        for conv in s.query(Conversation).filter(Conversation.workspace_id == wid).all():
            s.delete(conv)
        s.delete(ws)
        s.commit()


def test_list_conversations_desc_order(client, ws_id):
    ids = []
    for _ in range(3):
        resp = client.post(f"/api/workspaces/{ws_id}/conversations")
        assert resp.status_code == 201
        ids.append(resp.json()["id"])
    resp = client.get(f"/api/workspaces/{ws_id}/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["id"] for c in body] == sorted(ids, reverse=True)
    first = body[0]
    assert first["workspace_id"] == ws_id
    assert first["title"] == "新对话"
    assert first["created_at"]


def test_list_conversations_isolated_by_workspace(client, ws_id):
    resp = client.post(f"/api/workspaces/{ws_id}/conversations")
    cid = resp.json()["id"]
    with SessionLocal() as s:
        other = Workspace(name=f"conv-api-{uuid4().hex[:8]}")
        s.add(other)
        s.commit()
        other_id = other.id
    try:
        resp = client.get(f"/api/workspaces/{other_id}/conversations")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        with SessionLocal() as s:
            s.delete(s.get(Workspace, other_id))
            s.commit()
    assert client.delete(f"/api/conversations/{cid}").status_code == 204


def test_update_conversation_title(client, ws_id):
    cid = client.post(f"/api/workspaces/{ws_id}/conversations").json()["id"]
    resp = client.put(f"/api/conversations/{cid}", json={"title": "  退款问题  "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "退款问题"
    listed = client.get(f"/api/workspaces/{ws_id}/conversations").json()
    assert [c["title"] for c in listed if c["id"] == cid] == ["退款问题"]


@pytest.mark.parametrize("title", ["", "   ", "字" * 201])
def test_update_conversation_title_invalid_422(client, ws_id, title):
    cid = client.post(f"/api/workspaces/{ws_id}/conversations").json()["id"]
    resp = client.put(f"/api/conversations/{cid}", json={"title": title})
    assert resp.status_code == 422


def test_update_missing_conversation_404(client):
    assert client.put("/api/conversations/999999", json={"title": "x"}).status_code == 404
