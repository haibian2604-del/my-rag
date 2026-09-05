"""真实 rerank provider（oMLX /rerank 契约）测试。

单元：OMLXRerank 解析响应、按 results 顺序取 index、HTTP 错误抛中文 RuntimeError。
集成：retrieve 接入 openai_compat rerank（mock 把第 2 条排第一 / 500 降级 / 跳过零请求）。
"""
import asyncio
from uuid import uuid4

import pytest

from app.core.db import SessionLocal
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace
from app.providers.embedding.fake import FakeEmbedding
from app.providers.rerank.omlx import OMLXRerank
from app.services.retrieval.search import retrieve

QUERY = "退款政策几天可以退款"
REFUND_TEXT = "本店退款政策：签收后 7 天内可申请退款。"
SHIP_TEXT = "本店发货时间为工作日 48 小时内。"
POINTS_TEXT = "会员积分每年 1 月 1 日清零。"


# ---------- 单元：OMLXRerank ----------


async def test_omlx_rerank_returns_indexes_in_results_order(httpx_mock):
    # 乱序返回验证：按 results 顺序取 index，不做本地重排
    httpx_mock.add_response(json={"results": [
        {"index": 2, "relevance_score": 0.9, "document": {"text": "c"}},
        {"index": 0, "relevance_score": 0.5, "document": {"text": "a"}},
        {"index": 1, "relevance_score": 0.1, "document": {"text": "b"}},
    ], "usage": {"total_tokens": 10}})
    r = OMLXRerank(base_url="http://x/v1", model="reranker", api_key="sk-test")
    assert await r.rerank("q", ["a", "b", "c"], top_n=3) == [2, 0, 1]
    req = httpx_mock.get_requests()[0]
    assert req.url.path.endswith("/rerank")
    assert req.headers["Authorization"] == "Bearer sk-test"
    body = b"".join(req.stream) if hasattr(req, "stream") else req.content
    assert b'"top_n":3' in body.replace(b" ", b"")


async def test_omlx_rerank_http_500_raises_runtime_error(httpx_mock):
    httpx_mock.add_response(status_code=500, text="boom")
    r = OMLXRerank(base_url="http://x/v1", model="reranker")
    with pytest.raises(RuntimeError, match="rerank 请求失败"):
        await r.rerank("q", ["a"], top_n=1)


# ---------- 集成：retrieve 接入 ----------


@pytest.fixture
def rerank_seed():
    fake = FakeEmbedding(dim=4)
    qvec = asyncio.run(fake.embed([QUERY]))[0]
    ship_vec, points_vec = asyncio.run(fake.embed([SHIP_TEXT, POINTS_TEXT]))

    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        emb_cfg = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                                 is_default=True, params={"dim": 4})
        rerank_cfg = ProviderConfig(kind="rerank", provider="openai_compat",
                                    base_url="http://mock/v1", model="reranker",
                                    is_default=True)
        s.add_all([ws, emb_cfg, rerank_cfg])
        s.flush()

        doc = Document(workspace_id=ws.id, filename="refund.md", source_type="upload",
                       mime="text/markdown", size=10, checksum=f"rer-{uuid4()}", status="ready")
        s.add(doc)
        s.flush()
        chunks = []
        for i, content in enumerate([REFUND_TEXT, SHIP_TEXT, POINTS_TEXT]):
            c = Chunk(document_id=doc.id, workspace_id=ws.id, ordinal=i, content=content,
                      token_count=10, heading_path="", page_no=1)
            s.add(c)
            s.flush()
            chunks.append(c)
        s.add_all([
            ChunkEmbedding(chunk_id=chunks[0].id, workspace_id=ws.id, model_name="fake",
                           dim=4, embedding=qvec),  # 距离 0，召回必第一
            ChunkEmbedding(chunk_id=chunks[1].id, workspace_id=ws.id, model_name="fake",
                           dim=4, embedding=ship_vec),
            ChunkEmbedding(chunk_id=chunks[2].id, workspace_id=ws.id, model_name="fake",
                           dim=4, embedding=points_vec),
        ])
        s.commit()

        yield {"ws": ws, "chunk_ids": [c.id for c in chunks]}

        s.delete(s.get(Workspace, ws.id))
        s.delete(s.get(ProviderConfig, emb_cfg.id))
        s.delete(s.get(ProviderConfig, rerank_cfg.id))
        s.commit()


async def test_retrieve_with_rerank_reorders_hits(rerank_seed, httpx_mock):
    # 召回顺序 [refund, ship, points]；rerank 把第 2 条（ship, index=1）排第一
    httpx_mock.add_response(json={"results": [
        {"index": 1, "relevance_score": 0.9},
        {"index": 0, "relevance_score": 0.5},
        {"index": 2, "relevance_score": 0.1},
    ]})
    hits = await retrieve(rerank_seed["ws"].id, QUERY, use_rerank=True, top_k=3, top_n=3)
    assert [h["chunk_id"] for h in hits] == [rerank_seed["chunk_ids"][1],
                                             rerank_seed["chunk_ids"][0],
                                             rerank_seed["chunk_ids"][2]]


async def test_retrieve_rerank_truncates_top_n(rerank_seed, httpx_mock):
    httpx_mock.add_response(json={"results": [{"index": 2, "relevance_score": 0.9}]})
    hits = await retrieve(rerank_seed["ws"].id, QUERY, use_rerank=True, top_k=3, top_n=1)
    assert len(hits) == 1
    assert hits[0]["chunk_id"] == rerank_seed["chunk_ids"][2]


async def test_retrieve_rerank_500_degrades_to_original_order(rerank_seed, httpx_mock):
    httpx_mock.add_response(status_code=500, text="boom")
    hits = await retrieve(rerank_seed["ws"].id, QUERY, use_rerank=True, top_k=3, top_n=3)
    # 降级：不抛异常，返回召回原序（refund 距离 0 排第一）
    assert [h["chunk_id"] for h in hits] == rerank_seed["chunk_ids"]
    assert "退款" in hits[0]["content"]


async def test_retrieve_use_rerank_false_skips_http(rerank_seed, httpx_mock):
    hits = await retrieve(rerank_seed["ws"].id, QUERY, use_rerank=False, top_k=3)
    assert len(hits) == 3
    assert httpx_mock.get_requests() == []
