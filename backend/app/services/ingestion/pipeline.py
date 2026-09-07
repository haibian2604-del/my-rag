"""摄取管线：解析 → 切分 → 嵌入 → 入库，附状态机。

状态：pending → parsing → embedding → ready；任一步异常置 failed 并写 error。
"""
import logging
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig
from app.providers.embedding.fake import FakeEmbedding
from app.providers.embedding.openai_compat import OpenAICompatEmbedding
from app.services.ingestion.chunking import split_parents_and_children
from app.services.ingestion.parsing import parse_file
from app.services.ingestion.tokenize import tokenize_for_fts
from app.services.providers_service import decrypt_api_key

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


def default_provider_or_none(s: Session, kind: str) -> ProviderConfig | None:
    """探测默认 provider：配了返回配置行，没配返回 None（不抛异常）。"""
    try:
        return get_default_provider(s, kind)
    except RuntimeError:
        return None


def build_embedding_provider(cfg: ProviderConfig):
    if cfg.provider == "fake":
        params = cfg.params or {}
        return FakeEmbedding(dim=params.get("dim", 4))
    params = cfg.params or {}
    return OpenAICompatEmbedding(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=decrypt_api_key(cfg),
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
            units = split_parents_and_children(blocks)
            doc.status = "embedding"
            s.commit()
            emb_cfg = get_default_provider(s, "embedding")
            provider = build_embedding_provider(emb_cfg)
            # 先删旧向量与旧块（幂等重跑）；ChunkEmbedding 无 document_id，经 chunk 关联删除
            s.execute(delete(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id.in_(select(Chunk.id).where(Chunk.document_id == document_id))
            ))
            s.execute(delete(Chunk).where(Chunk.document_id == document_id))
            # 父子分块写入：父块行（有子块时 fts 为 NULL，仅存全文）；子块行
            # parent_id 指向父块，fts 建在子块上。无子块的短父块自身即叶子，
            # fts 直接建在父块上（兼容旧文档语义）。
            embeddable: list[Chunk] = []  # 待嵌入的叶子块：有子块则仅子块，否则父块本身
            ordinal = 0

            def make_chunk(text: str, token_count: int, parent_id: int | None, fts) -> Chunk:
                return Chunk(
                    document_id=document_id, workspace_id=doc.workspace_id,
                    ordinal=ordinal, parent_id=parent_id,
                    content=text, token_count=token_count,
                    heading_path=heading_path, page_no=page_no, fts=fts,
                )

            for u in units:
                heading_path, page_no = u.get("heading_path", ""), u.get("page_no")
                parent = make_chunk(u["text"], u.get("token_count", 0), None,
                                    None if u["children"]
                                    else func.to_tsvector("simple", tokenize_for_fts(u["text"])))
                s.add(parent)
                s.flush()  # 取 parent.id 供子块外键引用
                ordinal += 1
                if u["children"]:
                    children = []
                    for c in u["children"]:
                        children.append(make_chunk(
                            c["text"], c.get("token_count", 0), parent.id,
                            func.to_tsvector("simple", tokenize_for_fts(c["text"]))))
                        ordinal += 1
                    s.add_all(children)
                    embeddable.extend(children)
                else:
                    embeddable.append(parent)
            s.commit()
            # 嵌入仅对叶子块（子块；无子块的短父块嵌父块自身）
            vectors = await provider.embed([c.content for c in embeddable])
            s.add_all([
                ChunkEmbedding(chunk_id=c.id, workspace_id=c.workspace_id,
                               model_name=emb_cfg.model, dim=len(v), embedding=v)
                for c, v in zip(embeddable, vectors)
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
        # 摘要生成（M5-T2）：放在状态置 ready 之后独立 try，任何失败只记日志，
        # summary 保持 NULL，绝不影响 ready 状态
        try:
            from app.services.summary_service import generate_document_summary
            await generate_document_summary(document_id)
        except Exception:  # noqa: BLE001 — 摘要失败降级为不显示
            logger.warning("文档 %s 摘要生成失败（不影响 ready）", document_id, exc_info=True)
