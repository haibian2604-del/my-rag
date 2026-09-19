"""动态切分与动态 top_k 测试：按文档大小自适应切分参数；按切片大小缩放召回数。"""
from uuid import uuid4

from app.core.db import SessionLocal
from app.models.entities import Chunk, Document, Workspace
from app.services.ingestion.chunking import auto_chunk_params, split_parents_and_children
from app.services.retrieval.search import dynamic_top_k


def test_auto_chunk_params_thresholds():
    assert auto_chunk_params(1999) == (300, 120, 0.15)   # 短文档：小切片
    assert auto_chunk_params(2000) == (500, 200, 0.10)   # 中等：原默认
    assert auto_chunk_params(20000) == (500, 200, 0.10)
    assert auto_chunk_params(20001) == (800, 320, 0.08)  # 长文档：大切片


def test_split_short_doc_uses_small_chunks():
    """短文档：自适应参数下父/子块都小于原默认（重叠计入下一块，放行 15% 余量）。"""
    sent = "这句话用来测试动态切分逻辑。"  # 14 字符 ≈ 7 token，避免逐句取整放大偏差
    blocks = [{"text": sent * 200, "heading_path": "h", "page_no": None}]  # ~1400 token
    units = split_parents_and_children(blocks, *auto_chunk_params(1400))
    assert units
    assert all(u["token_count"] <= 345 for u in units)
    children = [c for u in units for c in u["children"]]
    assert children and all(c["token_count"] <= 138 for c in children)


def _seed_leaf_chunks(ws_id: int, token_counts: list[int]):
    """每个 token_count 建一个「父块+子块」对，叶子（子块）平均大小决定缩放。"""
    with SessionLocal() as s:
        doc = Document(workspace_id=ws_id, filename=f"d-{uuid4()}.md", source_type="upload",
                       mime="text/markdown", size=10, checksum=f"c-{uuid4()}", status="ready")
        s.add(doc)
        s.flush()
        for i, tc in enumerate(token_counts):
            parent = Chunk(document_id=doc.id, workspace_id=ws_id, ordinal=i * 2,
                           parent_id=None, content="p" * tc, token_count=tc,
                           heading_path="", page_no=None)
            s.add(parent)
            s.flush()
            s.add(Chunk(document_id=doc.id, workspace_id=ws_id, ordinal=i * 2 + 1,
                        parent_id=parent.id, content="x" * tc, token_count=tc,
                        heading_path="", page_no=None))
        s.commit()


def test_dynamic_top_k_scales_with_chunk_size():
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        s.add(ws)
        s.commit()
        wid = ws.id

    def _clear_chunks():
        with SessionLocal() as s:
            from sqlalchemy import delete
            s.execute(delete(Chunk).where(Chunk.workspace_id == wid))
            s.execute(delete(Document).where(Document.workspace_id == wid))
            s.commit()

    try:
        # 空库：保持 base_k
        assert dynamic_top_k(5, wid) == 5
        _seed_leaf_chunks(wid, [100] * 4)          # 平均 100：5×2=10（封顶）
        assert dynamic_top_k(5, wid) == 10
        _clear_chunks()
        _seed_leaf_chunks(wid, [400] * 4)          # 平均 400：5×0.5=2.5→3（兜底）
        assert dynamic_top_k(5, wid) == 3
        _clear_chunks()
        _seed_leaf_chunks(wid, [200] * 4)          # 平均 200：不缩放
        assert dynamic_top_k(5, wid) == 5
    finally:
        _clear_chunks()
        with SessionLocal() as s:
            if ws := s.get(Workspace, wid):
                s.delete(ws)
                s.commit()
