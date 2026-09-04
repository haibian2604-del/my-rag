"""检索管线测试：向量召回排序 / workspace 隔离 / status=ready 过滤 / 上下文标号。

fixture 采用手工插入 Chunk + ChunkEmbedding（比走完整 ingest 管线更快，且可
精确控制向量保证排序确定性）。查询向量经 FakeEmbedding 计算，命中块的向量
直接取查询向量（距离 0），其余块用各自内容的真实 FakeEmbedding 向量。
"""
import asyncio
from uuid import uuid4

import pytest

from app.core.db import SessionLocal
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace
from app.providers.embedding.fake import FakeEmbedding
from app.services.retrieval.context import build_context
from app.services.retrieval.search import search

QUERY = "退款政策几天可以退款"


def _make_doc(s, ws_id, filename, status):
    doc = Document(workspace_id=ws_id, filename=filename, source_type="upload",
                   mime="text/markdown", size=10, checksum=f"{filename}-{uuid4()}", status=status)
    s.add(doc)
    s.flush()
    return doc


def _make_chunk(s, doc, ordinal, content, heading_path="售后/退款", page_no=1):
    c = Chunk(document_id=doc.id, workspace_id=doc.workspace_id, ordinal=ordinal,
              content=content, token_count=10, heading_path=heading_path, page_no=page_no)
    s.add(c)
    s.flush()
    return c


def _make_embedding(s, chunk, model, vec):
    s.add(ChunkEmbedding(chunk_id=chunk.id, workspace_id=chunk.workspace_id,
                         model_name=model, dim=len(vec), embedding=vec))


@pytest.fixture
def seed_data():
    fake = FakeEmbedding(dim=4)
    refund_text = "本店退款政策：签收后 7 天内可申请退款，15 天内可换货。"
    ship_text = "本店发货时间为工作日 48 小时内，偏远地区除外。"
    points_text = "会员积分每年 1 月 1 日清零，请及时兑换。"
    qvec = asyncio.run(fake.embed([QUERY]))[0]
    ship_vec, points_vec = asyncio.run(fake.embed([ship_text, points_text]))

    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        other_ws = Workspace(name=f"ws-other-{uuid4()}")
        emb_cfg = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                                 is_default=True, params={"dim": 4})
        s.add_all([ws, other_ws, emb_cfg])
        s.flush()

        doc = _make_doc(s, ws.id, "refund.md", "ready")
        doc_failed = _make_doc(s, ws.id, "stale.md", "failed")  # embed 失败残留旧 chunk
        doc_other = _make_doc(s, other_ws.id, "other.md", "ready")

        c_refund = _make_chunk(s, doc, 0, refund_text)
        c_ship = _make_chunk(s, doc, 1, ship_text, heading_path="发货", page_no=2)
        c_points = _make_chunk(s, doc, 2, points_text, heading_path="会员", page_no=3)
        c_stale = _make_chunk(s, doc_failed, 0, refund_text)
        c_otherws = _make_chunk(s, doc_other, 0, refund_text)

        _make_embedding(s, c_refund, "fake", qvec)  # 距离 0，必排第一
        _make_embedding(s, c_ship, "fake", ship_vec)
        _make_embedding(s, c_points, "fake", points_vec)
        _make_embedding(s, c_stale, "fake", qvec)
        _make_embedding(s, c_otherws, "fake", qvec)
        s.commit()

        yield {
            "ws": ws, "other_ws": other_ws,
            "doc_id": doc.id, "other_doc_id": doc_other.id,
            "refund_chunk_id": c_refund.id,
        }

        # 清理：workspace 级联删除文档/块/向量；删除本 fixture 建的 provider
        for w in (ws, other_ws):
            s.delete(s.get(Workspace, w.id))
        s.delete(s.get(ProviderConfig, emb_cfg.id))
        s.commit()


async def test_search_orders_by_similarity(seed_data):
    hits = await search(seed_data["ws"].id, QUERY, top_k=3)
    assert len(hits) == 3
    assert "退款" in hits[0]["content"]
    # score=1-distance（相似度），按距离升序返回 ⇒ 相似度降序
    assert hits[0]["score"] >= hits[1]["score"] >= hits[2]["score"]


async def test_search_filters_non_ready_documents(seed_data):
    hits = await search(seed_data["ws"].id, QUERY, top_k=10)
    docs = {h["document_id"] for h in hits}
    from sqlalchemy import select

    from app.models.entities import Document
    with SessionLocal() as s:
        statuses = {d.id: d.status for d in s.execute(
            select(Document).where(Document.id.in_(docs))).scalars()}
    # failed 文档的残留 chunk（内容与命中块相同）不得命中
    assert all(st == "ready" for st in statuses.values())


async def test_workspace_isolation(seed_data):
    hits = await search(seed_data["other_ws"].id, QUERY, top_k=10)
    assert len(hits) > 0
    assert all(h["document_id"] != seed_data["doc_id"] for h in hits)


async def test_search_hit_shape(seed_data):
    hits = await search(seed_data["ws"].id, QUERY, top_k=1)
    h = hits[0]
    assert set(h) == {"chunk_id", "document_id", "filename", "content",
                      "heading_path", "page_no", "score"}
    assert h["filename"] == "refund.md"
    assert h["heading_path"] == "售后/退款"
    assert h["page_no"] == 1
    assert 0.0 <= h["score"] <= 1.0


async def test_build_context_numbering(seed_data):
    hits = await search(seed_data["ws"].id, QUERY, top_k=3)
    ctx, cites = build_context(hits)
    assert "[1]" in ctx and "[2]" in ctx and "[3]" in ctx
    assert len(cites) == len(hits)
    assert cites[0]["n"] == 1
    assert cites[0]["filename"] == "refund.md"
    assert len(cites[0]["snippet"]) <= 200


async def test_build_context_max_tokens(seed_data):
    hits = await search(seed_data["ws"].id, QUERY, top_k=3)
    _ctx, cites = build_context(hits, max_tokens=10)  # 20 字符，仅容得下第一块
    assert len(cites) == 1


async def test_retrieve_without_rerank_config_returns_search_order(seed_data):
    from app.services.retrieval.search import retrieve
    hits = await retrieve(seed_data["ws"].id, QUERY, use_rerank=True)
    direct = await search(seed_data["ws"].id, QUERY, top_k=5)
    assert [h["chunk_id"] for h in hits] == [h["chunk_id"] for h in direct]

    hits2 = await retrieve(seed_data["ws"].id, QUERY, use_rerank=False)
    assert [h["chunk_id"] for h in hits2] == [h["chunk_id"] for h in direct]
