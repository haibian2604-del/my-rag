"""FAQ 知识库模式测试（M5-T4）：标记检测、三种问答形态解析、降级、
父子结构落库（父块全文=问+答、子块=问题、heading_path=FAQ 标题）、
检索命中 FAQ 返回完整问答父块。
"""
import shutil
import uuid

import pytest
from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.db import SessionLocal
from app.jobs.runner import run_ingestion_sync
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace
from app.services.ingestion.chunking import split_faq_blocks
from app.services.ingestion.parsing import parse_file
from app.services.retrieval.search import retrieve


# ---------- 解析层 ----------


def test_faq_marker_qa_pairs(tmp_path):
    """<!-- faq --> 标记 + Q:/A: 行对：每对一个块，全文=问+答，heading_path=FAQ 标题。"""
    p = tmp_path / "help.md"
    p.write_text(
        "<!-- faq -->\n"
        "# 常见问题\n"
        "Q: 如何重置密码？\n"
        "A: 在登录页点击「忘记密码」，按邮件指引操作。\n"
        "\n"
        "Q: 支持哪些付款方式？\n"
        "A: 支持支付宝、微信与银行卡。\n",
        encoding="utf-8",
    )
    blocks = parse_file(p, "text/markdown")
    assert len(blocks) == 2
    assert blocks[0]["text"] == "如何重置密码？\n在登录页点击「忘记密码」，按邮件指引操作。"
    assert blocks[0]["heading_path"] == "FAQ: 如何重置密码？"
    assert blocks[0]["faq_question"] == "如何重置密码？"
    assert blocks[1]["heading_path"].startswith("FAQ: 支持哪些付款方式")


def test_faq_bold_wen_da_and_multiline(tmp_path):
    """**问**/**答** 形态与多行答案：续行并入答案。"""
    p = tmp_path / "bold.md"
    p.write_text(
        "<!-- faq -->\n"
        "**问**：什么是混合检索？\n"
        "**答**：向量检索与关键词检索结合。\n"
        "两路召回后融合排序。\n",
        encoding="utf-8",
    )
    blocks = parse_file(p, "text/markdown")
    assert len(blocks) == 1
    assert blocks[0]["faq_question"] == "什么是混合检索？"
    assert "两路召回后融合排序" in blocks[0]["text"]


def test_faq_table_form(tmp_path):
    """两列 markdown 表格（| 问题 | 答案 |）：跳过表头与分隔行，每行一对。"""
    p = tmp_path / "table.md"
    p.write_text(
        "<!-- faq -->\n"
        "| 问题 | 答案 |\n"
        "| --- | --- |\n"
        "| 退货流程是什么？ | 在订单页申请退货，寄回后退款。 |\n"
        "| 发票怎么开？ | 下单时备注发票抬头即可。 |\n",
        encoding="utf-8",
    )
    blocks = parse_file(p, "text/markdown")
    assert len(blocks) == 2
    assert blocks[0]["text"] == "退货流程是什么？\n在订单页申请退货，寄回后退款。"
    assert blocks[1]["heading_path"] == "FAQ: 发票怎么开？"
    assert all(b["faq_question"] for b in blocks)


def test_faq_marker_but_no_pairs_falls_back(tmp_path):
    """标记存在但解析不出 Q/A 对 → 降级为普通 markdown 解析。"""
    p = tmp_path / "nomark.md"
    p.write_text("# 说明\n这里只是一段普通正文，没有任何问答对。\n", encoding="utf-8")
    head = p.read_text(encoding="utf-8")
    assert "<!-- faq -->" not in head  # 前置确认：无标记
    blocks = parse_file(p, "text/markdown")
    assert blocks[0]["heading_path"] == "说明"
    assert all("faq_question" not in b for b in blocks)

    # 有标记但内容无问答对：同样降级
    p2 = tmp_path / "marked.md"
    p2.write_text("<!-- faq -->\n# 说明\n只有普通正文。\n", encoding="utf-8")
    blocks2 = parse_file(p2, "text/markdown")
    assert blocks2[0]["heading_path"] == "说明"
    assert all("faq_question" not in b for b in blocks2)


def test_faq_long_question_heading_truncated_to_30():
    """heading_path 取问题前 30 字。"""
    q = "这是一个非常非常长的问题" * 5
    from app.services.ingestion.parsing import _faq_block
    b = _faq_block(q, "答案")
    assert b["heading_path"] == f"FAQ: {q[:30]}"


# ---------- 切分层 ----------


def test_split_faq_blocks_structure():
    blocks = [
        {"text": "甲？\n乙。", "page_no": None, "heading_path": "FAQ: 甲？", "faq_question": "甲？"},
        {"text": "丙？\n丁。", "page_no": None, "heading_path": "FAQ: 丙？", "faq_question": "丙？"},
    ]
    units = split_faq_blocks(blocks)
    assert len(units) == 2  # 每个 FAQ 对独立父块，不聚合
    u = units[0]
    assert u["text"] == "甲？\n乙。"
    assert u["heading_path"] == "FAQ: 甲？"
    assert u["children"] == [{
        "text": "甲？", "heading_path": "FAQ: 甲？", "page_no": None,
        "token_count": u["children"][0]["token_count"],
    }]


# ---------- 摄取与检索 ----------


@pytest.fixture
def ws_with_fake_embedding():
    with SessionLocal() as s:
        ws = Workspace(name=f"faq-{uuid.uuid4()}")
        s.add(ws)
        provider = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                                  is_default=True, params={"dim": 4})
        s.add(provider)
        s.commit()
        yield ws
        s.delete(provider)
        s.delete(ws)
        s.commit()


def _ingest(ws_id, tmp_path, filename, content):
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    with SessionLocal() as s:
        doc = Document(workspace_id=ws_id, filename=filename, source_type="upload",
                       mime="text/markdown", size=p.stat().st_size,
                       checksum=f"faq-{filename}-{uuid.uuid4()}", status="pending")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    dest = settings.storage_dir / "documents" / f"{doc_id}_{filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(p, dest)
    try:
        run_ingestion_sync(doc_id)
    finally:
        dest.unlink(missing_ok=True)
    return doc_id


def test_faq_ingest_parent_child_structure(ws_with_fake_embedding, tmp_path):
    """摄取后：父块全文=问+答且无 fts/嵌入，子块=问题且 fts+嵌入齐全，heading=FAQ 标题。"""
    doc_id = _ingest(
        ws_with_fake_embedding.id, tmp_path, "faq.md",
        "<!-- faq -->\n"
        "Q: 退款政策是怎样的？\n"
        "A: 签收后 7 天内可申请退款。\n"
        "Q: 如何联系客服？\n"
        "A: 工作日 9 点到 18 点在线客服。\n",
    )
    try:
        with SessionLocal() as s:
            d = s.get(Document, doc_id)
            assert d.status == "ready"
            rows = s.execute(text(
                """
                SELECT c.id, c.parent_id, c.content, c.heading_path, c.fts IS NOT NULL AS has_fts,
                       (SELECT count(*) FROM chunk_embeddings ce
                         WHERE ce.chunk_id = c.id) AS emb_count
                FROM chunks c WHERE c.document_id = :did ORDER BY c.ordinal
                """
            ), {"did": doc_id}).mappings().all()
            parents = [r for r in rows if r["parent_id"] is None]
            children = [r for r in rows if r["parent_id"] is not None]
            assert len(parents) == 2 and len(children) == 2
            assert parents[0]["content"] == "退款政策是怎样的？\n签收后 7 天内可申请退款。"
            assert parents[0]["heading_path"] == "FAQ: 退款政策是怎样的？"
            assert children[0]["content"] == "退款政策是怎样的？"
            assert children[0]["heading_path"] == "FAQ: 退款政策是怎样的？"
            # 检索单元只在子块：父块无 fts/嵌入，子块 fts+嵌入齐全
            assert all(not r["has_fts"] and r["emb_count"] == 0 for r in parents)
            assert all(r["has_fts"] and r["emb_count"] == 1 for r in children)
            s.delete(d)
            s.commit()
    except Exception:
        raise


def test_faq_retrieval_returns_full_qa_parent(ws_with_fake_embedding, tmp_path):
    """检索命中 FAQ 子块 → 聚合返回完整问答父块，heading_path 即 FAQ 标题。"""
    import asyncio

    q1 = "退货流程是什么？"
    doc_id = _ingest(
        ws_with_fake_embedding.id, tmp_path, "faq.md",
        "<!-- faq -->\n"
        "| 问题 | 答案 |\n"
        "| --- | --- |\n"
        f"| {q1} | 在订单页申请退货，寄回后退款。 |\n"
        "| 发票怎么开？ | 下单时备注发票抬头即可。 |\n",
    )
    try:
        hits = asyncio.run(retrieve(ws_with_fake_embedding.id, q1, use_rerank=False,
                                    top_k=5, hybrid=False))
        assert hits, "应命中 FAQ 子块"
        top = hits[0]
        assert top["content"] == f"{q1}\n在订单页申请退货，寄回后退款。"  # 完整问答父块
        assert top["heading_path"] == f"FAQ: {q1}"
        with SessionLocal() as s:
            s.delete(s.get(Document, doc_id))
            s.commit()
    finally:
        pass


def test_unmarked_document_uses_normal_pipeline(ws_with_fake_embedding, tmp_path):
    """无标记文档走普通解析/父子切分，不受 FAQ 特性影响。"""
    doc_id = _ingest(
        ws_with_fake_embedding.id, tmp_path, "note.md",
        "# 标题\n这是正文内容，足够长以便切分。" * 30,
    )
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        assert d.status == "ready"
        chunks = s.execute(select(Chunk).where(Chunk.document_id == doc_id)).scalars().all()
        assert chunks
        assert all(c.heading_path != "" and not c.heading_path.startswith("FAQ:") for c in chunks)
        emb = s.execute(select(func.count()).select_from(ChunkEmbedding)).scalar()
        assert emb > 0
        s.delete(d)
        s.commit()
