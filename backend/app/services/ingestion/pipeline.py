"""摄取管线：解析 → 切分 → 嵌入 → 入库，附状态机。

状态：pending → parsing → embedding → ready；任一步异常置 failed 并写 error。
"""
from pathlib import Path
import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig
from app.providers.embedding.fake import FakeEmbedding
from app.providers.embedding.openai_compat import OpenAICompatEmbedding
from app.services.ingestion.chunking import split_blocks
from app.services.ingestion.parsing import parse_file

logger = logging.getLogger(__name__)


def doc_file_path(doc: Document) -> Path:
    return settings.storage_dir / "documents" / f"{doc.id}_{doc.filename}"


def get_default_provider(s: Session, kind: str) -> ProviderConfig:
    cfg = s.execute(
        select(ProviderConfig)
        .where(ProviderConfig.kind == kind, ProviderConfig.is_default.is_(True))
        .order_by(ProviderConfig.id)
    ).scalars().first()
    if not cfg:
        raise RuntimeError("未配置嵌入模型" if kind == "embedding" else f"未配置 {kind} 模型")
    return cfg


def build_embedding_provider(cfg: ProviderConfig):
    if cfg.provider == "fake":
        params = cfg.params or {}
        return FakeEmbedding(dim=params.get("dim", 4))
    api_key = None
    if cfg.api_key_encrypted:
        from app.core.security import decrypt_secret

        api_key = decrypt_secret(cfg.api_key_encrypted)
    params = cfg.params or {}
    return OpenAICompatEmbedding(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=api_key,
        batch_size=params.get("batch_size", 16),
        timeout=params.get("timeout", 120.0),
    )


async def ingest_document(document_id: int) -> None:
    with SessionLocal() as s:
        doc = s.get(Document, document_id)
        if not doc:
            return
        try:
            doc.status = "parsing"
            s.commit()
            blocks = parse_file(doc_file_path(doc), doc.mime)
            chunks = split_blocks(blocks)
            doc.status = "embedding"
            s.commit()
            emb_cfg = get_default_provider(s, "embedding")
            provider = build_embedding_provider(emb_cfg)
            # 先删旧向量与旧块（幂等重跑）；ChunkEmbedding 无 document_id，经 chunk 关联删除
            s.execute(delete(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id.in_(select(Chunk.id).where(Chunk.document_id == document_id))
            ))
            s.execute(delete(Chunk).where(Chunk.document_id == document_id))
            s.add_all([
                Chunk(document_id=document_id, workspace_id=doc.workspace_id,
                      ordinal=i, content=c["text"], token_count=c.get("token_count", 0),
                      heading_path=c.get("heading_path", ""), page_no=c.get("page_no"))
                for i, c in enumerate(chunks)
            ])
            s.commit()
            db_chunks = s.execute(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal)
            ).scalars().all()
            vectors = await provider.embed([c.content for c in db_chunks])
            s.add_all([
                ChunkEmbedding(chunk_id=c.id, workspace_id=c.workspace_id,
                               model_name=emb_cfg.model, dim=len(v), embedding=v)
                for c, v in zip(db_chunks, vectors)
            ])
            doc.status = "ready"
            doc.error = None
            s.commit()
        except Exception as e:
            s.rollback()
            doc = s.get(Document, document_id)
            if doc:
                doc.status = "failed"
                doc.error = str(e)[:2000]
                s.commit()
            logger.error("文档 %s 摄取失败: %s", document_id, e)
            raise
