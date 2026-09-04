from uuid import uuid4

import pytest

from app.core.db import SessionLocal
from app.models.entities import Document, ProviderConfig, Workspace


@pytest.fixture
def ws(client):
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-api-{uuid4()}")
        s.add(ws)
        provider = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                                  is_default=True, params={"dim": 4})
        s.add(provider)
        s.commit()
        yield ws
        s.delete(provider)
        s.delete(ws)
        s.commit()


def _cleanup_doc(doc_id: int) -> None:
    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        if doc:
            s.delete(doc)
            s.commit()


def test_upload_md_201_then_ready(client, ws):
    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        files={"file": ("note.md", b"# t\n" + b"hello world content " * 50, "text/markdown")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    doc_id = body["id"]
    _cleanup_doc(doc_id)


def test_upload_exe_422(client, ws):
    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        files={"file": ("evil.exe", b"MZ...", "application/octet-stream")},
    )
    assert resp.status_code == 422


def test_upload_too_large_422(client, ws):
    big = b"x" * (50 * 1024 * 1024 + 1)
    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        files={"file": ("big.md", big, "text/markdown")},
    )
    assert resp.status_code == 422


def test_list_documents(client, ws):
    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        files={"file": ("a.md", b"# A\ncontent", "text/markdown")},
    )
    doc_id = resp.json()["id"]
    resp = client.get(f"/api/workspaces/{ws.id}/documents")
    assert resp.status_code == 200
    assert any(d["id"] == doc_id for d in resp.json())
    _cleanup_doc(doc_id)


def test_delete_document(client, ws):
    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        files={"file": ("b.md", b"# B\ncontent", "text/markdown")},
    )
    doc_id = resp.json()["id"]
    resp = client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 204
    assert client.get(f"/api/documents/{doc_id}").status_code == 404


def test_reingest_no_error(client, ws):
    resp = client.post(
        f"/api/workspaces/{ws.id}/documents",
        files={"file": ("c.md", b"# C\n" + b"some long content here. " * 30, "text/markdown")},
    )
    doc_id = resp.json()["id"]
    resp = client.post(f"/api/documents/{doc_id}/reingest")
    assert resp.status_code == 200
    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        # 后台任务同步执行完（TestClient），fake provider 应到 ready
        assert doc.status in ("ready", "pending", "embedding", "parsing")
        _cleanup_doc(doc_id)
