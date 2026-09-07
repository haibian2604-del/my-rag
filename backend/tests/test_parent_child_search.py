"""父子分块检索测试：叶子过滤 / 父块聚合去重 / 新旧混合兼容 / rerank 后聚合。

fixture 手工插入父块（parent_id 空）+ 子块（parent_id 指向父块）+ 旧式叶子块
（无父子结构），嵌入仅落在子块与旧式叶子上（与 ingest 管线一致）。查询向量
直接赋给目标子块（距离 0）保证命中确定性。
"""
import asyncio
import shutil
from uuid import uuid4

import pytest
from sqlalchemy import func, text

from app.core.config import settings
from app.core.db import SessionLocal
from app.jobs.runner import run_ingestion_sync
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace
from app.providers.embedding.fake import FakeEmbedding
from app.services.ingestion.tokenize import tokenize_for_fts
from app.services.retrieval.search import _fts_recall, retrieve, search

QUERY = "退款政策几天可以退款"
PARENT_TEXT = (
    "本店售后政策总则：签收后 7 天内可申请退款，15 天内可换货，"
    "质量问题承担往返运费，非质量问题买家自行承担运费，"
    "定制商品不支持无理由退货，最终解释权归店家所有。"
)
CHILD1_TEXT = "子块一：签收后 7 天内可申请退款，需保留包装与凭证。"
CHILD2_TEXT = "子块二：15 天内可换货，需商品完好不影响二次销售。"
LEGACY_TEXT = "旧式叶子块：本店发货时间为工作日 48 小时内。"
DISTRACT_TEXT = "会员积分每年 1 月 1 日清零，请及时兑换。"


def _make_doc(s, ws_id, filename):
    doc = Document(workspace_id=ws_id, filename=filename, source_type="upload",
                   mime="text/markdown", size=10, checksum=f"pc-{filename}-{uuid4()}",
                   status="ready")
    s.add(doc)
    s.flush()
    return doc


def _make_chunk(s, doc, ordinal, content, parent_id=None, heading_path="售后", page_no=1,
                with_fts=False):
    kwargs = {}
    if with_fts:
        kwargs["fts"] = func.to_tsvector("simple", tokenize_for_fts(content))
    c = Chunk(document_id=doc.id, workspace_id=doc.workspace_id, ordinal=ordinal,
              content=content, token_count=10, heading_path=heading_path,
              page_no=page_no, parent_id=parent_id, **kwargs)
    s.add(c)
    s.flush()
    return c


def _make_embedding(s, chunk, vec):
    s.add(ChunkEmbedding(chunk_id=chunk.id, workspace_id=chunk.workspace_id,
                         model_name="fake", dim=len(vec), embedding=vec))


@pytest.fixture
def pc_seed():
    """新文档（父块 + 两子块）+ 旧文档（无父子结构的叶子块），嵌入仅叶子。"""
    fake = FakeEmbedding(dim=4)
    qvec = asyncio.run(fake.embed([QUERY]))[0]
    child2_vec = asyncio.run(fake.embed([CHILD2_TEXT]))[0]
    legacy_vec = asyncio.run(fake.embed([LEGACY_TEXT]))[0]
    distract_vec = asyncio.run(fake.embed([DISTRACT_TEXT]))[0]

    with SessionLocal() as s:
        ws = Workspace(name=f"pc-{uuid4()}")
        emb_cfg = ProviderConfig(kind="embedding", provider="fake", base_url="",
                                 model="fake", is_default=True, params={"dim": 4})
        s.add_all([ws, emb_cfg])
        s.flush()

        doc_new = _make_doc(s, ws.id, "new.md")
        doc_legacy = _make_doc(s, ws.id, "legacy.md")

        # 新文档：父块 P（无嵌入）+ 两子块（唯一被嵌入的检索单元）
        parent = _make_chunk(s, doc_new, 0, PARENT_TEXT, heading_path="售后/总则", page_no=1)
        c1 = _make_chunk(s, doc_new, 1, CHILD1_TEXT, parent_id=parent.id,
                         heading_path="售后/总则", page_no=1, with_fts=True)
        c2 = _make_chunk(s, doc_new, 2, CHILD2_TEXT, parent_id=parent.id,
                         heading_path="售后/总则", page_no=2, with_fts=True)
        # 旧文档：未回填的叶子块（parent_id 空、无子块），行为应与从前完全一致
        legacy = _make_chunk(s, doc_legacy, 0, LEGACY_TEXT, heading_path="发货", page_no=5,
                             with_fts=True)
        distract = _make_chunk(s, doc_legacy, 1, DISTRACT_TEXT, heading_path="会员", page_no=6)

        _make_embedding(s, c1, qvec)          # 距离 0，相似度 1.0
        _make_embedding(s, c2, child2_vec)
        _make_embedding(s, legacy, legacy_vec)
        _make_embedding(s, distract, distract_vec)
        s.commit()

        yield {"ws": ws, "parent_id": parent.id, "c1_id": c1.id, "c2_id": c2.id,
               "legacy_id": legacy.id}

        s.delete(s.get(Workspace, ws.id))
        s.delete(s.get(ProviderConfig, emb_cfg.id))
        s.commit()


# ---------- 叶子过滤（向量路与 FTS 路） ----------


async def test_vector_search_excludes_parent_with_children(pc_seed):
    """有子块的父块自身不被向量召回，子块才是检索单元。"""
    hits = await search(pc_seed["ws"].id, QUERY, top_k=10, hybrid=False)
    ids = {h["chunk_id"] for h in hits}
    assert pc_seed["parent_id"] not in ids
    assert {pc_seed["c1_id"], pc_seed["c2_id"]} <= ids


async def test_legacy_leaf_still_retrievable_in_mixed_workspace(pc_seed):
    """新旧混合工作区：未回填的旧叶子块（无子块指向自己）仍被召回，行为不变。"""
    hits = await search(pc_seed["ws"].id, QUERY, top_k=10, hybrid=False)
    ids = {h["chunk_id"] for h in hits}
    assert pc_seed["legacy_id"] in ids


def test_fts_recall_leaf_filter(pc_seed):
    """FTS 路同样按叶子原则过滤：有子块的块不召回，子块与旧叶子召回。"""
    with SessionLocal() as s:
        # 给父块也建 fts（模拟回填前后的混合态），断言它仍被叶子过滤排除
        s.execute(text(
            "UPDATE chunks SET fts = to_tsvector('simple', :tok) WHERE id = :i"
        ), {"tok": tokenize_for_fts(PARENT_TEXT), "i": pc_seed["parent_id"]})
        s.commit()
        # websearch_to_tsquery 对空格是隐式 AND，逐词召回后取并集
        ids: set[int] = set()
        for word in ("退款", "换货", "发货", "总则"):
            ids |= set(_fts_recall(s, pc_seed["ws"].id, tokenize_for_fts(word), limit=10))
    assert pc_seed["parent_id"] not in ids
    assert {pc_seed["c1_id"], pc_seed["c2_id"], pc_seed["legacy_id"]} <= ids


# ---------- 父块聚合 ----------


async def test_retrieve_aggregates_children_by_parent(pc_seed):
    """同父多子块命中去重为一个父块命中：content/heading/page 取父块，
    score 取组内最高子块分，child_hits 记录命中子块数。"""
    raw = await search(pc_seed["ws"].id, QUERY, top_k=10, hybrid=False)
    scores = {h["chunk_id"]: h["score"] for h in raw}

    hits = await retrieve(pc_seed["ws"].id, QUERY, use_rerank=False, top_k=10, hybrid=False)
    by_id = {h["chunk_id"]: h for h in hits}

    # 两个子块命中聚合为一个父块命中
    assert pc_seed["c1_id"] not in by_id and pc_seed["c2_id"] not in by_id
    g = by_id[pc_seed["parent_id"]]
    assert g["content"] == PARENT_TEXT  # 父块全文
    assert g["heading_path"] == "售后/总则"  # 父块元数据
    assert g["page_no"] == 1
    assert g["child_hits"] == 2
    assert g["score"] == pytest.approx(max(scores[pc_seed["c1_id"]], scores[pc_seed["c2_id"]]))

    # 旧叶子块逐条透传，child_hits=1
    lg = by_id[pc_seed["legacy_id"]]
    assert lg["content"] == LEGACY_TEXT
    assert lg["child_hits"] == 1
    assert lg["score"] == pytest.approx(scores[pc_seed["legacy_id"]])


async def test_retrieve_aggregates_after_rerank(pc_seed, httpx_mock):
    """rerank 在子块列表上做（截断 top_n），聚合到父块在后。"""
    with SessionLocal() as s:
        rerank_cfg = ProviderConfig(kind="rerank", provider="openai_compat",
                                    base_url="http://mock/v1", model="reranker",
                                    is_default=True)
        s.add(rerank_cfg)
        s.commit()
        cfg_id = rerank_cfg.id
    try:
        raw = await search(pc_seed["ws"].id, QUERY, top_k=10, hybrid=False)
        order = [h["chunk_id"] for h in raw]
        idx_c2 = order.index(pc_seed["c2_id"])  # rerank 只保留子块二
        httpx_mock.add_response(json={"results": [
            {"index": idx_c2, "relevance_score": 0.9},
        ]})
        hits = await retrieve(pc_seed["ws"].id, QUERY, use_rerank=True,
                              top_k=10, top_n=1, hybrid=False)
    finally:
        with SessionLocal() as s:
            s.delete(s.get(ProviderConfig, cfg_id))
            s.commit()
    assert len(hits) == 1
    g = hits[0]
    assert g["chunk_id"] == pc_seed["parent_id"]  # 子块命中聚合到父块
    assert g["content"] == PARENT_TEXT
    assert g["child_hits"] == 1
    assert httpx_mock.get_requests()[0].url.path.endswith("/rerank")


def test_aggregate_degrades_on_lookup_failure(monkeypatch):
    """父块信息补查失败 → 降级返回未聚合的子块命中，检索不失败。"""
    import app.services.retrieval.search as search_mod

    def _boom(*_a, **_k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(search_mod, "SessionLocal", _boom)
    hits = [{"chunk_id": 1, "document_id": 1, "filename": "a.md", "content": "子块内容",
             "heading_path": "甲", "page_no": 1, "score": 0.9}]
    assert search_mod._aggregate_parents(hits) == hits


# ---------- 摄取管线（父子结构落库） ----------


def test_ingest_writes_parent_child_structure(pc_seed, tmp_path):
    """长文档摄取：父块行 fts 为空且无嵌入，子块行 fts+嵌入齐全；短文档父块即叶子。"""
    p = tmp_path / "long.md"
    p.write_text("# 售后\n这是很长的句子。" * 200, encoding="utf-8")  # ≈900 token
    with SessionLocal() as s:
        doc = Document(workspace_id=pc_seed["ws"].id, filename="long.md",
                       source_type="upload", mime="text/markdown", size=p.stat().st_size,
                       checksum=f"long-{uuid4()}", status="pending")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    dest = settings.storage_dir / "documents" / f"{doc_id}_long.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(p, dest)
    try:
        run_ingestion_sync(doc_id)
    finally:
        dest.unlink(missing_ok=True)
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        assert d.status == "ready"
        rows = s.execute(
            text(
                """
                SELECT c.id, c.parent_id, c.fts IS NOT NULL AS has_fts,
                       (SELECT count(*) FROM chunk_embeddings ce
                         WHERE ce.chunk_id = c.id) AS emb_count
                FROM chunks c WHERE c.document_id = :did ORDER BY c.ordinal
                """
            ),
            {"did": doc_id},
        ).mappings().all()
        assert rows, "应有 chunk 落库"
        parent_ids = {r["id"] for r in rows if r["parent_id"] is None}
        child_ids = {r["id"] for r in rows if r["parent_id"] is not None}
        assert parent_ids and child_ids  # 长文档必须切出父块与子块
        parent_rows = [r for r in rows if r["id"] in parent_ids]
        child_rows = [r for r in rows if r["id"] in child_ids]
        # 有子块的父块：fts 为空、无嵌入（仅存全文）
        assert all(not r["has_fts"] and r["emb_count"] == 0 for r in parent_rows)
        # 子块：fts 与嵌入齐全（唯一被检索单元）
        assert all(r["has_fts"] and r["emb_count"] == 1 for r in child_rows)
        s.delete(d)
        s.commit()
