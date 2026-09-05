"""检索管线：向量召回 → 可选重排 → 上下文组装。

Hit 结构：{"chunk_id","document_id","filename","content","heading_path","page_no","score"}
score = 1 - 余弦距离（即余弦相似度），结果按相似度降序返回。
"""
import logging

from sqlalchemy import text

from app.core.db import SessionLocal
from app.providers.rerank.omlx import OMLXRerank
from app.services.ingestion.pipeline import build_embedding_provider, get_default_provider

logger = logging.getLogger(__name__)

Hit = dict


def build_rerank_provider(cfg):
    """根据 rerank provider 配置构造重排器（模式同 build_embedding_provider）。"""
    if cfg.provider == "openai_compat":
        from app.services.providers_service import decrypt_api_key
        return OMLXRerank(base_url=cfg.base_url, model=cfg.model,
                          api_key=decrypt_api_key(cfg))
    raise RuntimeError(f"不支持的 rerank provider：{cfg.provider}")


def _to_pgvector_string(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:g}" for x in vec) + "]"


def _rerank_score(query: str, content: str) -> float:
    q = set(query)
    if not q:
        return 0.0
    return len(q & set(content)) / len(q)


def _rerank_hits(query: str, hits: list[Hit], top_n: int) -> list[Hit]:
    return sorted(hits, key=lambda h: _rerank_score(query, h["content"]), reverse=True)[:top_n]


async def search(
    workspace_id: int,
    query: str,
    top_k: int = 5,
    score_threshold: float = 0.0,
) -> list[Hit]:
    with SessionLocal() as s:
        emb_cfg = get_default_provider(s, "embedding")
        provider = build_embedding_provider(emb_cfg)
        (qvec,) = await provider.embed([query])
        rows = s.execute(
            text(
                """
                SELECT c.id AS chunk_id, c.document_id, d.filename, c.content,
                       c.heading_path, c.page_no,
                       (ce.embedding <=> :qvec) AS distance
                FROM chunk_embeddings ce
                JOIN chunks c ON c.id = ce.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE ce.workspace_id = :ws_id
                  AND ce.model_name = :model
                  AND d.status = 'ready'
                ORDER BY ce.embedding <=> :qvec
                LIMIT :limit
                """
            ),
            {
                "qvec": _to_pgvector_string(qvec),
                "ws_id": workspace_id,
                "model": emb_cfg.model,
                "limit": top_k,
            },
        ).mappings().all()
    hits = []
    for r in rows:
        score = 1.0 - float(r["distance"])
        if score < score_threshold:
            continue
        hits.append({
            "chunk_id": r["chunk_id"],
            "document_id": r["document_id"],
            "filename": r["filename"],
            "content": r["content"],
            "heading_path": r["heading_path"] or "",
            "page_no": r["page_no"],
            "score": score,
        })
    return hits


async def retrieve(
    workspace_id: int,
    query: str,
    use_rerank: bool | None = None,
    top_k: int = 5,
    top_n: int = 3,
    score_threshold: float = 0.0,
) -> list[Hit]:
    hits = await search(workspace_id, query, top_k=top_k, score_threshold=score_threshold)
    if use_rerank is False or not hits:
        return hits
    with SessionLocal() as s:
        try:
            cfg = get_default_provider(s, "rerank")
        except RuntimeError:
            return hits
    if cfg.provider == "fake":
        return _rerank_hits(query, hits, top_n)
    try:
        provider = build_rerank_provider(cfg)
        idxs = await provider.rerank(query, [h["content"] for h in hits], top_n)
        # rerank 只重排+截断 top_n，不扩大集合
        return [hits[i] for i in idxs]
    except Exception:  # rerank 失败降级，检索永不因 rerank 失败而失败
        logger.warning("rerank 调用失败，降级返回未重排结果", exc_info=True)
        return hits
