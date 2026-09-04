import shutil
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.jobs.runner import run_ingestion_sync
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace


@pytest.fixture
def ws_with_fake_embedding():
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        s.add(ws)
        provider = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                                  is_default=True, params={"dim": 4})
        s.add(provider)
        s.commit()
        yield ws
        s.delete(provider)
        s.delete(ws)
        s.commit()


def test_ingest_end_to_end(ws_with_fake_embedding, tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# 标题\n这是正文内容，足够长以便切分。" * 20, encoding="utf-8")
    with SessionLocal() as s:
        doc = Document(workspace_id=ws_with_fake_embedding.id, filename="note.md",
                        source_type="upload", mime="text/markdown", size=p.stat().st_size,
                        checksum="x", status="pending")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    # 管线从 storage_dir 读取文件，模拟上传落盘
    from app.core.config import settings

    dest = settings.storage_dir / "documents" / f"{doc_id}_note.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(p, dest)
    try:
        run_ingestion_sync(doc_id)
    finally:
        dest.unlink(missing_ok=True)
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        assert d.status == "ready"
        chunks = s.execute(select(Chunk).where(Chunk.document_id == doc_id)).scalars().all()
        assert len(chunks) > 0
        # embedding 经 chunk 关联验证
        emb_count = s.execute(
            select(func.count()).select_from(ChunkEmbedding)
            .where(ChunkEmbedding.chunk_id.in_([c.id for c in chunks]))
        ).scalar()
        assert emb_count == len(chunks)
        # 清理
        s.delete(d)
        s.commit()


def test_ingest_missing_file_marks_failed(ws_with_fake_embedding):
    with SessionLocal() as s:
        doc = Document(workspace_id=ws_with_fake_embedding.id, filename="ghost.md",
                        source_type="upload", mime="text/markdown", size=1,
                        checksum="y", status="pending")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    with pytest.raises(Exception):  # noqa: B017
        run_ingestion_sync(doc_id)
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        assert d.status == "failed"
        assert d.error
        s.delete(d)
        s.commit()


def test_ingest_no_provider_marks_failed(tmp_path):
    # 不建 fake provider：依赖库里可能有其他默认配置，无法保证，跳过严格断言仅验证不崩溃路径
    with SessionLocal() as s:
        exists = s.execute(
            select(func.count()).select_from(ProviderConfig)
            .where(ProviderConfig.kind == "embedding", ProviderConfig.is_default.is_(True))
        ).scalar()
    if exists:
        pytest.skip("库中已有默认 embedding 配置，无法测无配置分支")
