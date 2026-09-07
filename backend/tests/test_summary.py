"""文档自动摘要与预览测试（M5-T2）：迁移 / 摄取后摘要 / 失败降级 / 手动接口 / preview。"""
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect as sa_inspect

from alembic import command
from app.core.db import SessionLocal, engine
from app.models.entities import Document, ProviderConfig, Workspace
from app.services.ingestion.pipeline import doc_file_path

SUMMARY_REPLY = "本文介绍退款政策：签收后七天内可申请退款，特殊商品除外。"


@pytest.fixture
def ws(client):
    """独立工作区 + fake 默认嵌入 provider（上传摄取需要），测试后清理。"""
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-sum-{uuid4()}")
        emb = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                             is_default=True, params={"dim": 4})
        s.add_all([ws, emb])
        s.commit()
        yield ws
        s.delete(emb)
        s.delete(ws)
        s.commit()


def _setup_llm(reply: str | None) -> int | None:
    """配置（或跳过）fake 默认 LLM。reply=None 表示不配置。"""
    if reply is None:
        return None
    with SessionLocal() as s:
        cfg = ProviderConfig(kind="llm", provider="fake", base_url="", model="fake",
                             is_default=True, params={"reply": reply})
        s.add(cfg)
        s.commit()
        return cfg.id


def _cleanup(llm_id: int | None, doc_id: int | None = None):
    with SessionLocal() as s:
        if doc_id and s.get(Document, doc_id):
            s.delete(s.get(Document, doc_id))
        if llm_id and s.get(ProviderConfig, llm_id):
            s.delete(s.get(ProviderConfig, llm_id))
        s.commit()


def _upload(client, ws_id: int, name: str, body: bytes) -> int:
    resp = client.post(
        f"/api/workspaces/{ws_id}/documents",
        files={"file": (name, body, "text/markdown")},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ---------- 迁移 ----------


def test_migration_add_and_drop_summary_column():
    """迁移可升可降：downgrade 后 documents 无 summary 列，重新 upgrade 后恢复。"""
    cfg = Config("alembic.ini")
    cols = lambda: {c["name"] for c in sa_inspect(engine).get_columns("documents")}
    assert "summary" in cols()  # 当前 head 应已含 summary 列
    # 显式降到 summary 迁移的上一版 e7f8a9b0c1d2（head 上可能有更新的迁移，-1 不再指向它）
    command.downgrade(cfg, "e7f8a9b0c1d2")
    assert "summary" not in cols()
    command.upgrade(cfg, "head")
    assert "summary" in cols()


# ---------- 摄取后摘要 ----------


def test_ingest_generates_summary(client, ws):
    """配置 fake LLM 后上传文档：摄取到 ready 且自动生成摘要。"""
    llm_id = _setup_llm(SUMMARY_REPLY)
    doc_id = _upload(client, ws.id, "refund.md", "# 退款政策\n七天内可申请退款。".encode())
    try:
        with SessionLocal() as s:
            doc = s.get(Document, doc_id)
            assert doc.status == "ready"
            assert doc.summary == SUMMARY_REPLY
    finally:
        _cleanup(llm_id, doc_id)


def test_ingest_no_llm_summary_null(client, ws):
    """未配置 LLM：摄取正常到 ready，summary 保持 NULL。"""
    doc_id = _upload(client, ws.id, "plain.md", "# 纯文本\n没有 LLM 也要能入库。".encode())
    try:
        with SessionLocal() as s:
            doc = s.get(Document, doc_id)
            assert doc.status == "ready"
            assert doc.summary is None
    finally:
        _cleanup(None, doc_id)


def test_ingest_llm_failure_summary_null_ready_kept(client, ws, monkeypatch):
    """LLM 调用失败（抛异常）：摘要降级为 NULL，文档仍为 ready。"""
    from app.services import summary_service

    async def _boom(llm, messages, timeout=None):
        raise RuntimeError("LLM 连接失败")

    monkeypatch.setattr(summary_service, "llm_complete", _boom)
    llm_id = _setup_llm(SUMMARY_REPLY)
    doc_id = _upload(client, ws.id, "boom.md", "# 报错场景\nLLM 挂了也不能影响 ready。".encode())
    try:
        with SessionLocal() as s:
            doc = s.get(Document, doc_id)
            assert doc.status == "ready"
            assert doc.summary is None
    finally:
        _cleanup(llm_id, doc_id)


# ---------- 手动摘要接口 ----------


def test_manual_summary_endpoint(client, ws):
    """POST /api/documents/{id}/summary 手动重新生成摘要；不存在 → 404。"""
    llm_id = _setup_llm(SUMMARY_REPLY)
    doc_id = _upload(client, ws.id, "manual.md", "# 手动摘要\n调用手动接口重新生成。".encode())
    try:
        resp = client.post(f"/api/documents/{doc_id}/summary")
        assert resp.status_code == 200
        assert resp.json()["summary"] == SUMMARY_REPLY
        assert client.post("/api/documents/99999999/summary").status_code == 404
    finally:
        _cleanup(llm_id, doc_id)


def test_manual_summary_degrades_without_llm(client, ws):
    """无 LLM 时手动接口不报错，summary 保持 NULL（降级）。"""
    doc_id = _upload(client, ws.id, "nollm.md", "# 无LLM\n手动触发也不该报错。".encode())
    try:
        resp = client.post(f"/api/documents/{doc_id}/summary")
        assert resp.status_code == 200
        assert resp.json()["summary"] is None
    finally:
        _cleanup(None, doc_id)


# ---------- 列表 summary / 详情 preview ----------


def test_list_has_summary_and_detail_preview(client, ws):
    """列表 DocumentOut 带 summary 字段；详情带 preview（原文前 500 字符，缺文件为空串）。"""
    content = "# 预览测试\n" + "这是正文内容。" * 100  # > 500 字符
    doc_id = _upload(client, ws.id, "preview.md", content.encode())
    try:
        docs = client.get(f"/api/workspaces/{ws.id}/documents").json()
        assert docs and all("summary" in d for d in docs)
        detail = client.get(f"/api/documents/{doc_id}")
        assert detail.status_code == 200
        assert detail.json()["preview"] == content[:500]
        # 原文文件被删后 preview 降级为空串（不报错）
        with SessionLocal() as s:
            path = doc_file_path(s.get(Document, doc_id))
        path.unlink()
        assert client.get(f"/api/documents/{doc_id}").json()["preview"] == ""
    finally:
        _cleanup(None, doc_id)
