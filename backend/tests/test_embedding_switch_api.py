"""嵌入切换/回滚 API 测试：fake provider + 手工向量，唯一前缀自清理。"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.main import create_app
from app.models.entities import (
    AppConfig,
    Chunk,
    ChunkEmbedding,
    Document,
    ProviderConfig,
    Workspace,
)
from app.services.embedding_switch import read_state, write_state


@pytest.fixture
def env():
    """fake embedding provider(old-model 默认) + old/new 向量各若干。"""
    uid = uuid4().hex[:8]
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-embsw-{uid}")
        s.add(ws)
        s.flush()
        doc = Document(
            workspace_id=ws.id, filename=f"doc-{uid}.txt",
            checksum=f"chk-{uid}", status="ready",
        )
        s.add(doc)
        s.flush()
        chunks = [
            Chunk(document_id=doc.id, workspace_id=ws.id,
                  ordinal=i, content=f"chunk {i}")
            for i in range(3)
        ]
        s.add_all(chunks)
        s.flush()
        s.add_all([
            ChunkEmbedding(chunk_id=c.id, workspace_id=ws.id,
                           model_name="old-model", dim=4, embedding=[0.1] * 4)
            for c in chunks
        ])
        s.add(ChunkEmbedding(chunk_id=chunks[0].id, workspace_id=ws.id,
                             model_name="new-model", dim=4, embedding=[0.2] * 4))
        p = ProviderConfig(kind="embedding", provider="fake",
                           base_url="http://mock", model="old-model",
                           is_default=True, params={"dim": 4})
        s.add(p)
        write_state(s, state="idle")
        s.commit()
    yield {"ws_id": ws.id, "provider_id": p.id, "chunk_ids": [c.id for c in chunks]}
    with SessionLocal() as s:
        s.execute(delete(ChunkEmbedding).where(
            ChunkEmbedding.workspace_id == ws.id))
        s.execute(delete(Chunk).where(Chunk.workspace_id == ws.id))
        s.execute(delete(Document).where(Document.workspace_id == ws.id))
        s.execute(delete(Workspace).where(Workspace.name == f"ws-embsw-{uid}"))
        s.execute(delete(ProviderConfig).where(ProviderConfig.id == p.id))
        s.execute(delete(AppConfig).where(AppConfig.key == "embedding_switch"))
        s.commit()


def test_switch_202_runs_reembed(client, env):
    resp = client.post("/api/settings/embedding/switch",
                       json={"target_model": "new-model"})
    assert resp.status_code == 202
    assert resp.json() == {"state": "running", "target_model": "new-model"}
    # BackgroundTasks 已执行完（TestClient 同步）：状态 done，向量已写入
    with SessionLocal() as s:
        state = read_state(s)
        assert state["state"] == "done"
        # 共享开发库可能存在其他 ready 文档，total 为全局值
        assert state["total"] >= 3 and state["done"] == state["total"]
        n = s.execute(
            select(ChunkEmbedding.id).where(
                ChunkEmbedding.model_name == "new-model",
                ChunkEmbedding.chunk_id.in_(env["chunk_ids"]),
            )
        ).scalars().all()
        assert len(n) == 3
        # provider 未被自动切换
        assert s.get(ProviderConfig, env["provider_id"]).model == "old-model"


def test_switch_409_when_running(client, env):
    with SessionLocal() as s:
        write_state(s, state="running", target_model="new-model", done=0)
        s.commit()
    resp = client.post("/api/settings/embedding/switch",
                       json={"target_model": "new-model"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "已有重嵌任务在运行"


def test_switch_same_model_400(client, env):
    resp = client.post("/api/settings/embedding/switch",
                       json={"target_model": "old-model"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "目标模型与当前一致"


def test_switch_empty_target_422(client, env):
    assert client.post("/api/settings/embedding/switch",
                       json={"target_model": ""}).status_code == 422


def test_get_switch_state(client, env):
    resp = client.get("/api/settings/embedding/switch")
    assert resp.status_code == 200
    assert resp.json()["current_model"] == "old-model"
    assert resp.json()["state"] == "idle"


def test_activate_without_vectors_404(client, env):
    resp = client.post("/api/settings/embedding/activate",
                       json={"model": "ghost-model"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "该模型暂无可用向量"
    with SessionLocal() as s:
        assert s.get(ProviderConfig, env["provider_id"]).model == "old-model"


def test_activate_switches_and_records_previous(client, env):
    resp = client.post("/api/settings/embedding/activate",
                       json={"model": "new-model"})
    assert resp.status_code == 200
    assert resp.json() == {"current_model": "new-model"}
    with SessionLocal() as s:
        p = s.get(ProviderConfig, env["provider_id"])
        assert p.model == "new-model"
        assert p.base_url == "http://mock"  # 其他字段不变
        assert p.params == {"dim": 4}
        assert read_state(s)["previous_model"] == "old-model"
    assert client.get("/api/settings/embedding/switch").json()["current_model"] == "new-model"


def test_get_switch_state_returns_previous_model(client, env):
    client.post("/api/settings/embedding/activate", json={"model": "new-model"})
    resp = client.get("/api/settings/embedding/switch")
    assert resp.status_code == 200
    assert resp.json()["previous_model"] == "old-model"
    # 未切换过时无 previous_model 字段（清掉 state 行后回归 idle）
    with SessionLocal() as s:
        row = s.get(AppConfig, "embedding_switch")
        if row:
            s.delete(row)
        s.commit()
    assert "previous_model" not in client.get("/api/settings/embedding/switch").json()


def test_activate_no_provider_404(client):
    # conftest 已在每条测试前临时摘除默认 provider 标记（不删行）：
    # 自建一条向量通过向量校验后，因无默认 embedding provider → 404
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-embsw-nop-{uuid4().hex[:8]}")
        s.add(ws)
        s.flush()
        doc = Document(workspace_id=ws.id, filename="d.txt",
                       checksum=f"chk-{uuid4().hex}", status="ready")
        s.add(doc)
        s.flush()
        chunk = Chunk(document_id=doc.id, workspace_id=ws.id,
                      ordinal=0, content="c")
        s.add(chunk)
        s.flush()
        emb = ChunkEmbedding(chunk_id=chunk.id, workspace_id=ws.id,
                             model_name="any-model", dim=4, embedding=[0.1] * 4)
        s.add(emb)
        s.commit()
        ws_id, emb_id = ws.id, emb.id
    try:
        resp = client.post("/api/settings/embedding/activate",
                           json={"model": "any-model"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "未配置嵌入模型"
    finally:
        with SessionLocal() as s:
            s.execute(delete(ChunkEmbedding).where(ChunkEmbedding.id == emb_id))
            s.execute(delete(Chunk).where(Chunk.workspace_id == ws_id))
            s.execute(delete(Document).where(Document.workspace_id == ws_id))
            s.execute(delete(Workspace).where(Workspace.id == ws_id))
            s.commit()


def test_rollback_back_to_old_model(client, env):
    # 回滚与切换共用 activate 端点（语义别名已删）
    assert client.post("/api/settings/embedding/activate",
                       json={"model": "new-model"}).status_code == 200
    resp = client.post("/api/settings/embedding/activate",
                       json={"model": "old-model"})
    assert resp.status_code == 200
    assert resp.json() == {"current_model": "old-model"}
    with SessionLocal() as s:
        assert s.get(ProviderConfig, env["provider_id"]).model == "old-model"
        assert read_state(s)["previous_model"] == "new-model"
