"""检索管线：向量召回（+ 可选 FTS 召回，RRF 融合）→ 可选重排 → 上下文组装。

Hit 结构：{"chunk_id","document_id","filename","content","heading_path","page_no","score"}
纯向量：score = 1 - 余弦距离（即余弦相似度），按相似度降序返回。
混合检索：score = RRF 融合分 Σ 1/(60 + rank_i)，按融合分降序返回。
score_threshold 只在纯向量模式下生效（语义为余弦相似度门槛）；hybrid 模式
下的 RRF 分与相似度不同量纲，不应用该阈值。
"""
import logging

from sqlalchemy import bindparam, text

from app.core.db import SessionLocal
from app.providers.rerank.omlx import OMLXRerank
from app.services.ingestion.pipeline import build_embedding_provider, get_default_provider
from app.services.ingestion.tokenize import tokenize_for_fts
from app.services.providers_service import decrypt_api_key

logger = logging.getLogger(__name__)

RRF_K = 60

# 叶子原则（父子分块，兼容旧数据）：检索单元 = parent_id 非空的子块，
# 或没有子块指向自己的块（旧文档未回填时父块自身即叶子，行为与从前一致）。
_LEAF_FILTER = """
              AND (c.parent_id IS NOT NULL
                   OR NOT EXISTS (SELECT 1 FROM chunks ch WHERE ch.parent_id = c.id))
"""


def build_rerank_provider(cfg):
    """根据 rerank provider 配置构造重排器（模式同 build_embedding_provider）。"""
    if cfg.provider == "openai_compat":
        return OMLXRerank(base_url=cfg.base_url, model=cfg.model,
                          api_key=decrypt_api_key(cfg))
    raise RuntimeError(f"不支持的 rerank provider：{cfg.provider}")


def _to_pgvector_string(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:g}" for x in vec) + "]"


def _fetch_chunk_fields(s, chunk_ids: list[int]) -> dict[int, dict]:
    """补取仅 FTS 路命中的 chunk 全字段（一次 IN 查询）。"""
    if not chunk_ids:
        return {}
    rows = s.execute(
        text(
            """
            SELECT c.id AS chunk_id, c.document_id, d.filename, c.content,
                   c.heading_path, c.page_no
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.id IN :ids
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": chunk_ids},
    ).mappings().all()
    return {r["chunk_id"]: dict(r) for r in rows}


def _fts_recall(s, workspace_id: int, tokens: str, limit: int) -> list[int]:
    """FTS 召回：ts_rank 排序的 chunk_id 列表（沿用 ready 与 workspace 过滤）。"""
    rows = s.execute(
        text(
            """
            SELECT c.id AS chunk_id,
                   ts_rank(c.fts, websearch_to_tsquery('simple', :tokens)) AS rank_score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.workspace_id = :ws_id
              AND d.status = 'ready'
              AND c.fts @@ websearch_to_tsquery('simple', :tokens)
""" + _LEAF_FILTER + """
            ORDER BY rank_score DESC
            LIMIT :limit
            """
        ),
        {"ws_id": workspace_id, "tokens": tokens, "limit": limit},
    ).mappings().all()
    return [r["chunk_id"] for r in rows]


def _rrf_fuse(vector_hits: list[dict], fts_ids: list[int], top_k: int) -> list[dict]:
    """RRF 融合：rrf(chunk_id) = Σ 1/(60 + rank)，rank 从 1 起，缺席不计。"""
    rrf: dict[int, float] = {}
    for rank, h in enumerate(vector_hits, 1):
        rrf[h["chunk_id"]] = rrf.get(h["chunk_id"], 0.0) + 1.0 / (RRF_K + rank)
    fts_only = [cid for cid in fts_ids if cid not in rrf]
    for rank, cid in enumerate(fts_ids, 1):
        rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (RRF_K + rank)
    # 仅 FTS 路命中的 chunk 补取字段：补查失败只丢弃这些 chunk，不影响向量路结果
    try:
        with SessionLocal() as s:
            fields = _fetch_chunk_fields(s, fts_only)
    except Exception:
        logger.warning("FTS 命中 chunk 字段补取失败，丢弃 FTS 独有命中", exc_info=True)
        fields = {}
        fts_only = []
    hits: dict[int, dict] = {h["chunk_id"]: h for h in vector_hits}
    for cid in fts_only:
        f = fields.get(cid)
        if not f:  # 极端情况（并发删除）：跳过
            continue
        hits[cid] = {
            "chunk_id": f["chunk_id"],
            "document_id": f["document_id"],
            "filename": f["filename"],
            "content": f["content"],
            "heading_path": f["heading_path"] or "",
            "page_no": f["page_no"],
        }
    fused = sorted(hits.values(), key=lambda h: rrf[h["chunk_id"]], reverse=True)
    for h in fused:
        h["score"] = rrf[h["chunk_id"]]
    return fused[:top_k]


def _aggregate_parents(hits: list[dict]) -> list[dict]:
    """子块命中按父块分组去重，返回结构保持不变。

    - chunk_id = 父块 id，content = 父块全文，heading_path/page_no 取父块；
    - score = 组内最高子块分；附带 child_hits = 组内命中子块数（供前端展示）；
    - 组间顺序 = 首次出现的顺序（输入已按相关性排序，不按 score 重排，
      以免破坏 rerank 的顺序语义）；
    - 旧文档无父子结构（全为叶子）时逐条原样映射，行为与从前完全一致；
    - 父块信息补查失败仅降级为未聚合结果，检索永不因此失败。
    """
    if not hits:
        return []
    try:
        with SessionLocal() as s:
            rows = s.execute(
                text(
                    """
                    SELECT c.id AS chunk_id, c.parent_id,
                           p.content AS parent_content,
                           p.heading_path AS parent_heading,
                           p.page_no AS parent_page
                    FROM chunks c
                    LEFT JOIN chunks p ON p.id = c.parent_id
                    WHERE c.id IN :ids
                    """
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": [h["chunk_id"] for h in hits]},
            ).mappings().all()
    except Exception:
        logger.warning("父块信息补取失败，返回未聚合的子块命中", exc_info=True)
        return hits
    info = {r["chunk_id"]: dict(r) for r in rows}
    groups: dict[int, dict] = {}
    order: list[int] = []
    for h in hits:
        r = info.get(h["chunk_id"])
        # 无子块的叶子（父块自身）：按自身透传；有 parent_id 则归到父块
        if r and r["parent_id"] is not None:
            pid = r["parent_id"]
            content = r["parent_content"] if r["parent_content"] is not None else h["content"]
            heading = r["parent_heading"] if r["parent_heading"] is not None else h["heading_path"]
            page = r["parent_page"] if r["parent_page"] is not None else h["page_no"]
        else:
            pid, content, heading, page = h["chunk_id"], h["content"], h["heading_path"], h["page_no"]
        if pid not in groups:
            groups[pid] = {
                "chunk_id": pid,
                "document_id": h["document_id"],
                "filename": h["filename"],
                "content": content,
                "heading_path": heading or "",
                "page_no": page,
                "score": h["score"],
                "child_hits": 0,
            }
            order.append(pid)
        g = groups[pid]
        g["child_hits"] += 1
        g["score"] = max(g["score"], h["score"])  # 组内最高子块分
    return [groups[pid] for pid in order]


async def search(
    workspace_id: int,
    query: str,
    top_k: int = 5,
    score_threshold: float = 0.0,
    hybrid: bool = True,
) -> list[dict]:
    with SessionLocal() as s:
        emb_cfg = get_default_provider(s, "embedding")
        provider = build_embedding_provider(emb_cfg)
        (qvec,) = await provider.embed([query])
        # 表达式部分索引要求查询侧 cast 与索引表达式完全一致才能命中索引：
        # 先取该模型在本工作区的 dim（一行 SELECT），无任何向量行则直接返回空列表
        dim = s.execute(
            text(
                "SELECT dim FROM chunk_embeddings "
                "WHERE model_name = :model AND workspace_id = :ws_id LIMIT 1"
            ),
            {"model": emb_cfg.model, "ws_id": workspace_id},
        ).scalar_one_or_none()
        if dim is None:
            return []
        dim = int(dim)  # 内部 int，f-string 内联安全
        rows = s.execute(
            text(
                f"""
                SELECT c.id AS chunk_id, c.document_id, d.filename, c.content,
                       c.heading_path, c.page_no,
                       (ce.embedding::vector({dim}) <=> :qvec) AS distance
                FROM chunk_embeddings ce
                JOIN chunks c ON c.id = ce.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE ce.workspace_id = :ws_id
                  AND ce.model_name = :model
                  AND ce.dim = :dim
                  AND d.status = 'ready'
""" + _LEAF_FILTER + f"""
                ORDER BY (ce.embedding::vector({dim}) <=> :qvec)
                LIMIT :limit
                """
            ),
            {
                "qvec": _to_pgvector_string(qvec),
                "ws_id": workspace_id,
                "model": emb_cfg.model,
                "dim": dim,
                "limit": top_k,
            },
        ).mappings().all()

        vector_hits = [
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "filename": r["filename"],
                "content": r["content"],
                "heading_path": r["heading_path"] or "",
                "page_no": r["page_no"],
                "score": 1.0 - float(r["distance"]),
            }
            for r in rows
        ]

        if not hybrid:
            hits = vector_hits
        else:
            # FTS 路：任何异常降级为纯向量结果，检索永不因 FTS 失败而失败
            try:
                tokens = tokenize_for_fts(query)
                fts_ids = _fts_recall(s, workspace_id, tokens, top_k) if tokens else []
            except Exception:
                logger.warning("FTS 召回失败，降级为纯向量结果", exc_info=True)
                fts_ids = []
            hits = _rrf_fuse(vector_hits, fts_ids, top_k)

    # threshold 语义 = 向量余弦相似度门槛，只对纯向量路生效。
    # hybrid 模式下 score 是 RRF 融合分（Σ 1/(60+rank)，两路融合上限约 0.033），
    # 与 0–1 的相似度量纲完全不同——若在此套用 threshold 会导致任何 >0.033 的
    # 阈值在默认 hybrid 模式下静默返回空，故 hybrid 时不应用 threshold。
    return [h for h in hits if hybrid or h["score"] >= score_threshold]


async def retrieve(
    workspace_id: int,
    query: str,
    use_rerank: bool | None = None,
    top_k: int = 5,
    top_n: int = 3,
    score_threshold: float = 0.0,
    hybrid: bool = True,
) -> list[dict]:
    hits = await search(workspace_id, query, top_k=top_k,
                        score_threshold=score_threshold, hybrid=hybrid)
    if use_rerank is False or not hits:
        return _aggregate_parents(hits)
    with SessionLocal() as s:
        try:
            cfg = get_default_provider(s, "rerank")
        except RuntimeError:
            return _aggregate_parents(hits)
    try:
        provider = build_rerank_provider(cfg)
        idxs = await provider.rerank(query, [h["content"] for h in hits], top_n)
        # rerank 在子块列表上只重排+截断 top_n，不扩大集合；聚合到父块在后
        reranked = [hits[i] for i in idxs]
    except Exception:  # rerank 失败降级，检索永不因 rerank 失败而失败
        logger.warning("rerank 调用失败，降级返回未重排结果", exc_info=True)
        reranked = hits
    return _aggregate_parents(reranked)
