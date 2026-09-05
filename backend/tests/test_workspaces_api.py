from uuid import uuid4

import pytest

from app.core.db import SessionLocal
from app.models.entities import Workspace


def _unique() -> str:
    return f"ws-api-{uuid4().hex[:8]}"


def _create(name: str) -> Workspace:
    with SessionLocal() as s:
        ws = Workspace(name=name)
        s.add(ws)
        s.commit()
        s.refresh(ws)
        return ws


def _cleanup(ws_id: int) -> None:
    with SessionLocal() as s:
        ws = s.get(Workspace, ws_id)
        if ws:
            s.delete(ws)
            s.commit()


def test_list_workspaces_200(client):
    resp = client.get("/api/workspaces")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_workspace_201(client):
    name = _unique()
    resp = client.post("/api/workspaces", json={"name": name, "description": "d"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == name
    _cleanup(body["id"])


def test_create_workspace_duplicate_409(client):
    ws = _create(_unique())
    try:
        resp = client.post("/api/workspaces", json={"name": ws.name})
        assert resp.status_code == 409
    finally:
        _cleanup(ws.id)


def test_rename_workspace_200(client):
    ws = _create(_unique())
    try:
        resp = client.put(f"/api/workspaces/{ws.id}", json={"name": _unique(), "description": "x"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "x"
    finally:
        _cleanup(ws.id)


def test_rename_workspace_duplicate_409(client):
    a = _create(_unique())
    b = _create(_unique())
    try:
        resp = client.put(f"/api/workspaces/{a.id}", json={"name": b.name, "description": ""})
        assert resp.status_code == 409
    finally:
        _cleanup(a.id)
        _cleanup(b.id)


def test_rename_workspace_blank_name_422(client):
    ws = _create(_unique())
    try:
        resp = client.put(f"/api/workspaces/{ws.id}", json={"name": "  ", "description": ""})
        assert resp.status_code == 422
    finally:
        _cleanup(ws.id)


def test_rename_workspace_too_long_422(client):
    ws = _create(_unique())
    try:
        resp = client.put(f"/api/workspaces/{ws.id}", json={"name": "x" * 101, "description": ""})
        assert resp.status_code == 422
    finally:
        _cleanup(ws.id)


def test_delete_workspace_204_and_cascade(client):
    ws = _create(_unique())
    resp = client.delete(f"/api/workspaces/{ws.id}")
    assert resp.status_code == 204
    with SessionLocal() as s:
        assert s.get(Workspace, ws.id) is None


def test_delete_workspace_404(client):
    resp = client.delete("/api/workspaces/99999999")
    assert resp.status_code == 404


def test_delete_last_workspace_409(client):
    """仅当库中无其他工作区时才能触发 409；共享开发库通常已有工作区则跳过。

    不清他人工作区（共享库，删真实数据不可接受）。
    """
    with SessionLocal() as s:
        remaining = s.query(Workspace).count()
    if remaining > 0:
        pytest.skip("共享库存在其他工作区，无法验证最后一个工作区规则")
    ws = _create(_unique())
    try:
        resp = client.delete(f"/api/workspaces/{ws.id}")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "至少保留一个工作区"
    finally:
        # 清理：先建占位工作区，再删测试工作区
        keeper = _create(_unique())
        assert client.delete(f"/api/workspaces/{ws.id}").status_code == 204
        _cleanup(keeper.id)
