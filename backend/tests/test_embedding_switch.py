import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import aliased

from app.core.db import SessionLocal
from app.jobs.runner import run_ingestion_sync
from app.models.entities import (
    AppConfig,
    Chunk,
    ChunkEmbedding,
    Document,
    ProviderConfig,
    Workspace,
)
from app.services.embedding_switch import read_state, reembed_all, write_state


@pytest.fixture
def ws_with_fake_embedding():
    name = f"reembed-{uuid4().hex[:8]}"
    with SessionLocal() as s:
        ws = Workspace(name=name)
        s.add(ws)
        provider = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake-embed",
                                  is_default=True, params={"dim": 4})
        s.add(provider)
        s.commit()
        yield ws
        s.delete(provider)
        s.delete(ws)
        s.commit()


def _make_doc(ws_id: int, filename: str, sentence: str) -> int:
    """建文档并走真实摄取管线，返回 doc_id。"""

    from app.core.config import settings

    with SessionLocal() as s:
        doc = Document(workspace_id=ws_id, filename=filename,
                        source_type="upload", mime="text/markdown",
                        size=1, checksum=uuid4().hex[:16], status="pending")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    dest = settings.storage_dir / "documents" / f"{doc_id}_{filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(sentence * 30, encoding="utf-8")
    try:
        run_ingestion_sync(doc_id)
    finally:
        dest.unlink(missing_ok=True)
    return doc_id


def test_reembed_all(ws_with_fake_embedding):
    ws = ws_with_fake_embedding
    d1 = _make_doc(ws.id, "a.md", "第一篇文档的独特句子，内容各不相同以便产生多个块。")
    d2 = _make_doc(ws.id, "b.md", "第二篇文档的独特句子，与第一篇完全不同的表述方式。")
    try:
        with SessionLocal() as s:
            docs = s.execute(select(Document).where(
                Document.workspace_id == ws.id, Document.status == "ready")).scalars().all()
            assert len(docs) == 2
            chunks = s.execute(select(Chunk).where(
                Chunk.document_id.in_([d.id for d in docs]))).scalars().all()
            # 父子分块后仅叶子块被嵌入：parent_id 非空的子块，或无子块指向的父块
            parent_ids = {c.parent_id for c in chunks if c.parent_id is not None}
            leaf_ids = [c.id for c in chunks
                        if c.parent_id is not None or c.id not in parent_ids]
            total = len(leaf_ids)
            assert 0 < total < len(chunks)  # 本 fixture 文档必有父块被切出子块
            # 全库 ready 叶子块总数（共享开发库可能有其他工作区的 ready 文档）；
            # reembed 只统计叶子块，父块不再计入 total
            _leaf = aliased(Chunk)
            grand_total = s.execute(
                select(func.count()).select_from(Chunk)
                .join(Document, Chunk.document_id == Document.id)
                .where(Document.status == "ready",
                       or_(Chunk.parent_id.isnot(None),
                           ~exists().where(_leaf.parent_id == Chunk.id)))
            ).scalar()
            # 摄取后叶子块向量 model_name=fake-embed
            old_count = s.execute(
                select(func.count()).select_from(ChunkEmbedding)
                .where(ChunkEmbedding.chunk_id.in_(leaf_ids),
                       ChunkEmbedding.model_name == "fake-embed")
            ).scalar()
            assert old_count == total

        asyncio.run(reembed_all("target-model-x"))

        with SessionLocal() as s:
            all_ids = [c.id for c in s.execute(select(Chunk).where(
                Chunk.document_id.in_([d1, d2]))).scalars().all()]
            parent_only = [cid for cid in all_ids if cid not in set(leaf_ids)]
            # 重嵌按叶子 chunk 逐条覆盖；父块不产生向量
            new_count = s.execute(
                select(func.count()).select_from(ChunkEmbedding)
                .where(ChunkEmbedding.chunk_id.in_(leaf_ids),
                       ChunkEmbedding.model_name == "target-model-x")
            ).scalar()
            assert new_count == total
            if parent_only:
                parent_vec = s.execute(
                    select(func.count()).select_from(ChunkEmbedding)
                    .where(ChunkEmbedding.chunk_id.in_(parent_only),
                           ChunkEmbedding.model_name == "target-model-x")
                ).scalar()
                assert parent_vec == 0
            # 旧向量仍在
            old_count = s.execute(
                select(func.count()).select_from(ChunkEmbedding)
                .where(ChunkEmbedding.chunk_id.in_(leaf_ids),
                       ChunkEmbedding.model_name == "fake-embed")
            ).scalar()
            assert old_count == total
            # 状态 done
            state = read_state(s)
            assert state["state"] == "done"
            assert state["total"] == grand_total
            assert state["done"] == grand_total

        # 幂等重跑
        asyncio.run(reembed_all("target-model-x"))
        with SessionLocal() as s:
            new_count = s.execute(
                select(func.count()).select_from(ChunkEmbedding)
                .where(ChunkEmbedding.chunk_id.in_(leaf_ids),
                       ChunkEmbedding.model_name == "target-model-x")
            ).scalar()
            assert new_count == total
    finally:
        with SessionLocal() as s:
            for did in (d1, d2):
                d = s.get(Document, did)
                if d:
                    s.delete(d)
            s.commit()


def test_reembed_rejects_when_running(ws_with_fake_embedding):
    with SessionLocal() as s:
        write_state(s, state="running", target_model="x")
        s.commit()
    try:
        with pytest.raises(RuntimeError, match="已有重嵌任务在运行"):
            asyncio.run(reembed_all("target-model-x"))
    finally:
        with SessionLocal() as s:
            row = s.get(AppConfig, "embedding_switch")
            if row:
                s.delete(row)
            s.commit()
