"""--compare 对比模式冒烟测试：evaluate_recall / print_compare_report 可跑通。

复用 test_hybrid_search 的 seed 思路：手工插入 Chunk + ChunkEmbedding（fake
hash 向量），使 FTS 路对特定关键词稳定命中。断言 evaluate_recall 返回结构
合法，且 hybrid 对关键词查询的召回不差于纯向量。
"""
import asyncio
import time
from uuid import uuid4

import pytest

from app.core.db import SessionLocal
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace
from app.providers.embedding.fake import FakeEmbedding

from tests.evaluation.run_eval import evaluate_recall, print_compare_report
from tests.test_hybrid_search import OTHER_TEXTS, QUERY, TARGET_TEXT, _make_chunk, _make_doc, _make_embedding

QUERIES = [{"query": QUERY, "expect_keywords": ["火山"]}]


@pytest.fixture
def eval_seed():
    fake = FakeEmbedding(dim=4)
    vectors = asyncio.run(fake.embed([TARGET_TEXT] + OTHER_TEXTS))
    with SessionLocal() as s:
        ws = Workspace(name=f"evalcmp-{uuid4()}")
        emb_cfg = ProviderConfig(kind="embedding", provider="fake", base_url="",
                                 model="fake", is_default=True, params={"dim": 4})
        s.add_all([ws, emb_cfg])
        s.flush()
        docs = [_make_doc(s, ws.id, f"doc{i}.md") for i in range(3)]
        chunks = [_make_chunk(s, docs[0], 0, TARGET_TEXT)]
        for i, t in enumerate(OTHER_TEXTS):
            chunks.append(_make_chunk(s, docs[i % 3], (i % 3) + 1, t))
        for c, v in zip(chunks, vectors):
            _make_embedding(s, c, "fake", v)
        s.commit()
        yield ws.id
        s.delete(s.get(Workspace, ws.id))
        s.delete(s.get(ProviderConfig, emb_cfg.id))
        s.commit()


def test_evaluate_recall_returns_flags_and_latencies(eval_seed):
    flags, latencies = evaluate_recall(eval_seed, QUERIES, top_k=5, hybrid=True)
    assert len(flags) == len(QUERIES)
    assert all(isinstance(f, bool) for f in flags)
    assert len(latencies) == len(QUERIES)
    assert all(l > 0 for l in latencies)
    # 目标关键词经 FTS 路在 hybrid 下稳定命中
    assert flags == [True]


def test_compare_hybrid_not_worse_than_vector(eval_seed, capsys):
    hflags, hlat = evaluate_recall(eval_seed, QUERIES, top_k=5, hybrid=True)
    vflags, vlat = evaluate_recall(eval_seed, QUERIES, top_k=5, hybrid=False)
    assert sum(hflags) >= sum(vflags)  # 验收标准：hybrid ≥ vector（允许持平）
    print_compare_report(QUERIES, 5, hflags, hlat, vflags, vlat)
    out = capsys.readouterr().out
    assert "hybrid" in out and "vector" in out
    assert "recall@5" in out
    assert "hybrid ≥ vector: 是" in out
