"""混合检索测试：jieba 分词 / FTS 召回 + RRF 融合 / NULL 容忍 / 异常降级 / backfill / 参数接线。

fixture 采用手工插入 Chunk（fts 经 to_tsvector('simple', jieba 分词) 写入，
模拟 ingest 行为）+ ChunkEmbedding。FakeEmbedding 是 hash 向量，与词面语义
无关：特定关键词的 FTS 路命中是确定的，纯向量路命中是随机的——以此构造
"hybrid 命中优于纯向量"的对比用例。workspace 名用唯一前缀 hybrid- 便于清理。
"""
import asyncio
import shutil
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.core.config import settings as app_settings  # noqa: F401
from app.core.db import SessionLocal
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace
from app.providers.embedding.fake import FakeEmbedding
from app.services.ingestion.backfill_fts import backfill_fts
from app.services.ingestion.tokenize import tokenize_for_fts
from app.services.retrieval.search import search

QUERY = "火山喷发"
TARGET_TEXT = (
    "火山喷发是地球内部岩浆冲破地壳的剧烈活动，火山喷发会喷出火山灰、"
    "岩浆与气体，历史上著名的火山喷发深刻改变了气候与地貌。"
    "喷发前常有地震前兆。火山灰肥沃了周围的土壤。科学家监测火山的活动。"
)
# hash 向量与词面相关：经实测（含 pgvector float4 存储精度）该文本使纯向量
# top1 落在无关块（大熊猫）、目标块向量 rank=5（仍在 top_k 内，可获 RRF 加成），
# 保证断言确定性。
OTHER_TEXTS = [
    "珊瑚礁是海洋中生物多样性最高的生态系统之一，珊瑚虫与虫黄藻共生形成珊瑚礁。",
    "中世纪骑士文化盛行于欧洲，骑士宣誓效忠领主，骑士制度随火药武器兴起而衰落。",
    "量子计算机利用量子比特的叠加与纠缠进行运算，量子计算机在特定问题上远超经典计算机。",
    "咖啡的风味取决于产地、烘焙度与冲煮方式，浅烘焙的咖啡往往带有明亮的果酸。",
    "大熊猫以竹子为主食，每天进食竹子可超过十小时，大熊猫是国家一级保护动物。",
]


def test_tokenize_for_fts_basic():
    tokens = tokenize_for_fts("火山喷发会喷出火山灰")
    assert tokens == " ".join(tokens.split())  # 空格 join，无多余空白
    assert "火山" in tokens.split() or "火山喷发" in tokens.split()
    assert "火山灰" in tokens.split()
    assert "" not in tokens.split()


def test_tokenize_for_fts_empty():
    assert tokenize_for_fts("") == ""
    assert tokenize_for_fts("   ") == ""


def _make_doc(s, ws_id, filename):
    doc = Document(workspace_id=ws_id, filename=filename, source_type="upload",
                   mime="text/markdown", size=10, checksum=f"{filename}-{uuid4()}",
                   status="ready")
    s.add(doc)
    s.flush()
    return doc


def _make_chunk(s, doc, ordinal, content, with_fts=True):
    kwargs = {}
    if with_fts:
        from sqlalchemy import func as sa_func

        from app.services.ingestion.tokenize import tokenize_for_fts as tok

        kwargs["fts"] = sa_func.to_tsvector("simple", tok(content))
    c = Chunk(document_id=doc.id, workspace_id=doc.workspace_id, ordinal=ordinal,
              content=content, token_count=10, heading_path="", page_no=None, **kwargs)
    s.add(c)
    s.flush()
    return c


def _make_embedding(s, chunk, model, vec):
    s.add(ChunkEmbedding(chunk_id=chunk.id, workspace_id=chunk.workspace_id,
                         model_name=model, dim=len(vec), embedding=vec))


@pytest.fixture
def hybrid_seed():
    fake = FakeEmbedding(dim=4)
    all_texts = [TARGET_TEXT] + OTHER_TEXTS
    vectors = asyncio.run(fake.embed(all_texts))
    qvec = asyncio.run(fake.embed([QUERY]))[0]

    with SessionLocal() as s:
        ws = Workspace(name=f"hybrid-{uuid4()}")
        emb_cfg = ProviderConfig(kind="embedding", provider="fake", base_url="",
                                 model="fake", is_default=True, params={"dim": 4})
        s.add_all([ws, emb_cfg])
        s.flush()

        docs = [_make_doc(s, ws.id, f"doc{i}.md") for i in range(3)]
        chunks = []
        chunks.append(_make_chunk(s, docs[0], 0, TARGET_TEXT))
        for i, t in enumerate(OTHER_TEXTS):
            chunks.append(_make_chunk(s, docs[i % 3], (i % 3) + 1, t))
        for c, v in zip(chunks, vectors):
            _make_embedding(s, c, "fake", v)
        s.commit()

        yield {"ws": ws, "target_chunk_id": chunks[0].id, "qvec": qvec,
               "chunk_ids": [c.id for c in chunks]}

        s.delete(s.get(Workspace, ws.id))
        s.delete(s.get(ProviderConfig, emb_cfg.id))
        s.commit()


async def test_hybrid_beats_pure_vector(hybrid_seed):
    ws_id = hybrid_seed["ws"].id
    # hybrid：FTS 路稳定命中含关键词的 chunk（hash 向量与词面无关，RRF 融合后必居首）
    hits = await search(ws_id, QUERY, top_k=5, hybrid=True)
    assert hits, "hybrid 检索应返回结果"
    assert QUERY.replace("喷发", "") in hits[0]["content"] or "火山" in hits[0]["content"]
    assert hits[0]["chunk_id"] == hybrid_seed["target_chunk_id"]
    assert hits[0]["score"] > 0  # rrf_score
    # 纯向量：hash 向量下特定词的命中是随机的——断言 top1 不含目标关键词
    # （若该断言因向量恰好命中而失败，属数据设计问题，应换关键词而非放宽断言）
    vhits = await search(ws_id, QUERY, top_k=5, hybrid=False)
    assert "火山" not in vhits[0]["content"]


async def test_hybrid_ignores_score_threshold(hybrid_seed):
    """hybrid 模式下不应用 score_threshold（RRF 分上限约 0.033，与相似度量纲不同）：
    阈值远大于 RRF 上限时检索仍返回结果。"""
    ws_id = hybrid_seed["ws"].id
    hits = await search(ws_id, QUERY, top_k=5, hybrid=True, score_threshold=0.5)
    assert hits, "hybrid 模式下 score_threshold 不应过滤 RRF 分"
    # 与 threshold=0 的结果完全一致：hybrid 下 threshold 不生效
    hits_no_threshold = await search(ws_id, QUERY, top_k=5, hybrid=True, score_threshold=0.0)
    assert [h["chunk_id"] for h in hits] == [h["chunk_id"] for h in hits_no_threshold]


async def test_hybrid_fallback_on_fts_error(hybrid_seed, monkeypatch):
    ws_id = hybrid_seed["ws"].id
    import app.services.retrieval.search as search_mod

    def _boom(_text):
        raise RuntimeError("jieba exploded")

    monkeypatch.setattr(search_mod, "tokenize_for_fts", _boom)
    hits = await search(ws_id, QUERY, top_k=5, hybrid=True)  # 不应抛异常
    vhits = await search(ws_id, QUERY, top_k=5, hybrid=False)
    assert [h["chunk_id"] for h in hits] == [h["chunk_id"] for h in vhits]


async def test_fts_null_chunk_tolerated(hybrid_seed):
    """fts=NULL 的旧 chunk 不进 FTS 路，但不报错且向量路仍可见。"""
    with SessionLocal() as s:
        doc = _make_doc(s, hybrid_seed["ws"].id, "legacy.md")
        c = _make_chunk(s, doc, 0, TARGET_TEXT, with_fts=False)  # 模拟旧数据
        _make_embedding(s, c, "fake", hybrid_seed["qvec"])  # 距离 0：向量路必排第一
        s.commit()
        legacy_id = c.id
    hits = await search(hybrid_seed["ws"].id, QUERY, top_k=10, hybrid=True)
    assert legacy_id in {h["chunk_id"] for h in hits}  # 向量路可见，hybrid 未报错


async def test_backfill_fts_idempotent(hybrid_seed):
    with SessionLocal() as s:
        doc = _make_doc(s, hybrid_seed["ws"].id, "backfill.md")
        c = _make_chunk(s, doc, 0, "回填测试：量子纠缠与贝尔不等式。", with_fts=False)
        s.commit()
        cid = c.id
        assert s.execute(text("SELECT fts IS NULL FROM chunks WHERE id=:i"),
                         {"i": cid}).scalar() is True
    n = backfill_fts()
    assert n >= 1
    with SessionLocal() as s:
        assert s.execute(text("SELECT fts IS NULL FROM chunks WHERE id=:i"),
                         {"i": cid}).scalar() is False
    # 幂等：再跑一次为 0 条
    assert backfill_fts() == 0


def test_ingest_writes_fts(tmp_path, hybrid_seed):
    """ingest 管线写入 fts 列（覆盖 pipeline 的 fts 表达式）。"""
    from app.core.config import settings
    from app.jobs.runner import run_ingestion_sync

    p = tmp_path / "volcano.md"
    p.write_text("# 火山\n火山喷发是剧烈的地质活动。" * 20, encoding="utf-8")
    with SessionLocal() as s:
        doc = Document(workspace_id=hybrid_seed["ws"].id, filename="volcano.md",
                       source_type="upload", mime="text/markdown", size=p.stat().st_size,
                       checksum=f"v-{uuid4()}", status="pending")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    dest = settings.storage_dir / "documents" / f"{doc_id}_volcano.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(p, dest)
    try:
        run_ingestion_sync(doc_id)
    finally:
        dest.unlink(missing_ok=True)
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        assert d.status == "ready"
        nulls = s.execute(
            select(func.count()).select_from(Chunk)
            .where(Chunk.document_id == doc_id, Chunk.fts.is_(None))
        ).scalar()
        assert nulls == 0
        s.delete(d)
        s.commit()


def test_fts_fetch_fields_failure_degrades(monkeypatch):
    """FTS 独有命中的字段补查异常 → 丢弃这些 chunk，只留向量路结果（不失败）。"""
    import app.services.retrieval.search as search_mod

    def _boom(_s, _ids):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(search_mod, "_fetch_chunk_fields", _boom)
    vector_hits = [{"chunk_id": 1, "document_id": 1, "filename": "a.md",
                    "content": "向量路命中", "heading_path": "", "page_no": None,
                    "score": 0.9}]
    fused = search_mod._rrf_fuse(vector_hits, [999], top_k=5)  # 999 仅 FTS 路命中
    assert [h["chunk_id"] for h in fused] == [1]  # 补查失败的 FTS-only chunk 被丢弃
    assert fused[0]["score"] > 0
