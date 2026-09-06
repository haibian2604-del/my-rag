from itertools import pairwise

from app.services.ingestion.chunking import split_blocks, split_parents_and_children


def test_merges_small_paragraphs_with_heading_path():
    blocks = [
        {"text": "段落一。", "page_no": None, "heading_path": "指南 > 安装"},
        {"text": "段落二。", "page_no": None, "heading_path": "指南 > 安装"},
    ]
    chunks = split_blocks(blocks, max_tokens=50)
    assert len(chunks) == 1
    assert chunks[0]["heading_path"] == "指南 > 安装"
    assert "段落一" in chunks[0]["text"] and "段落二" in chunks[0]["text"]


def test_long_paragraph_split_with_overlap():
    long_text = "这是很长的句子。" * 400  # 3200 chars ≈ 1600 token
    blocks = [{"text": long_text, "page_no": 3, "heading_path": "报告"}]
    chunks = split_blocks(blocks, max_tokens=500, overlap_ratio=0.1)
    assert len(chunks) >= 3
    assert all(c["token_count"] <= 500 + 50 for c in chunks)  # 容忍句子粒度误差
    assert all(c["heading_path"] == "报告" for c in chunks)
    assert chunks[0]["page_no"] == 3
    # 相邻块尾部重叠：后一块开头 = 前一块尾部
    assert chunks[1]["text"][:20] == chunks[0]["text"][-20:]
    overlap = set(chunks[0]["text"][-100:]) & set(chunks[1]["text"][:100])
    assert len(overlap) > 0


def test_different_heading_paths_split():
    blocks = [
        {"text": "甲节内容。", "page_no": None, "heading_path": "甲"},
        {"text": "乙节内容。", "page_no": None, "heading_path": "乙"},
    ]
    chunks = split_blocks(blocks, max_tokens=500)
    assert len(chunks) == 2
    assert chunks[0]["heading_path"] == "甲"
    assert chunks[1]["heading_path"] == "乙"


def test_no_leak_across_sections():
    long_text = "这是很长的句子。" * 200
    blocks = [
        {"text": long_text, "page_no": 1, "heading_path": "甲"},
        {"text": "乙节内容。", "page_no": 1, "heading_path": "乙"},
    ]
    chunks = split_blocks(blocks, max_tokens=500, overlap_ratio=0.1)
    assert chunks[-1]["heading_path"] == "乙"
    assert chunks[-1]["text"] == "乙节内容。"  # 不携带甲节的 overlap 残留
    for c in chunks:
        if "乙节内容" in c["text"]:
            assert c["heading_path"] == "乙"
            assert "这是很长的句子" not in c["text"]  # 甲节内容不混入乙节
    # 甲节内容只在其自己的块中，未被错误重复发射到乙节
    assert sum(1 for c in chunks if "乙节内容" in c["text"]) == 1


def test_tail_overlap_semantics():
    long_text = "这是很长的句子。" * 200
    chunks = split_blocks(
        [{"text": long_text, "page_no": None, "heading_path": "甲"}],
        max_tokens=500, overlap_ratio=0.1,
    )
    assert len(chunks) >= 2
    assert chunks[0]["text"][-20:] == chunks[1]["text"][:20]  # 后块开头复制前块尾部
    for prev, nxt in pairwise(chunks):
        assert nxt["text"][:20] == prev["text"][-20:]


def test_no_punct_long_sentence_not_repeated():
    text = "无标点超长内容" * 300  # 2100 chars，无任何句子标点
    chunks = split_blocks(
        [{"text": text, "page_no": None, "heading_path": "甲"}],
        max_tokens=500, overlap_ratio=0.1,
    )
    assert len(chunks) >= 2
    assert all(c["token_count"] <= 500 + 50 for c in chunks)
    # 窗口硬切：不允许出现互为重复的整句大块
    assert len({c["text"] for c in chunks}) == len(chunks)
    # 覆盖原文：末块应包含原文结尾，首块为原文开头
    assert chunks[0]["text"].startswith("无标点超长内容")
    assert text.endswith(chunks[-1]["text"][-20:])


def test_empty_blocks_skipped():
    blocks = [
        {"text": "", "page_no": None, "heading_path": "甲"},
        {"text": "   \n ", "page_no": None, "heading_path": "甲"},
        {"text": "正文。", "page_no": None, "heading_path": "甲"},
    ]
    chunks = split_blocks(blocks, max_tokens=500)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "正文。"
    assert chunks[0]["token_count"] == _expected_tokens("正文。")


def _expected_tokens(text: str) -> int:
    return max(1, len(text) // 2)


# ---------- 父子分块 ----------


def test_parent_child_mapping_and_child_bounds():
    """长文先聚合成父块，每个父块内部再切子块；子块受 child_tokens 约束。"""
    long_text = "这是很长的句子。" * 200  # 800 token，单节
    units = split_parents_and_children(
        [{"text": long_text, "page_no": 1, "heading_path": "甲"}],
        parent_tokens=500, child_tokens=200, overlap_ratio=0.1,
    )
    assert len(units) >= 2  # 800 token 至少 2 个父块
    for p in units:
        assert p["heading_path"] == "甲" and p["page_no"] == 1
        assert p["token_count"] <= 500 + 50  # 容忍句子粒度误差
        # 超过 child_tokens 的父块必须切出子块
        assert p["children"], "长父块应切出子块"
        for c in p["children"]:
            assert c["token_count"] <= 200 + 20  # 容忍句子粒度误差
            assert c["heading_path"] == p["heading_path"]  # 元数据继承
            assert c["page_no"] == p["page_no"]
        # 同节内相邻子块轻重叠（200 token × 0.1 = 40 字符）：后块开头 = 前块尾部
        for prev, nxt in pairwise(p["children"]):
            assert nxt["text"][:40] == prev["text"][-40:]
        # 子块内容覆盖父块：首块从父块开头起，末块到父块结尾止
        assert p["text"].startswith(p["children"][0]["text"][:20])
        assert p["text"].endswith(p["children"][-1]["text"][-20:])


def test_short_parent_is_leaf_without_children():
    """父块内容 ≤ child_tokens 时不切子块（children 为空），父块自身即检索单元。"""
    blocks = [
        {"text": "短段落一。", "page_no": None, "heading_path": "甲"},
        {"text": "短段落二。", "page_no": None, "heading_path": "甲"},
    ]
    units = split_parents_and_children(blocks, parent_tokens=500, child_tokens=200)
    assert len(units) == 1
    assert units[0]["children"] == []
    assert units[0]["text"] == "短段落一。短段落二。"


def test_children_overlap_not_across_sections():
    """子块重叠只发生在同一父块（同节）内；跨节的父块互不重叠、内容不串。"""
    long_text = "这是很长的句子。" * 200  # 甲节 1600 token
    blocks = [
        {"text": long_text, "page_no": 1, "heading_path": "甲"},
        {"text": "乙节内容。", "page_no": 2, "heading_path": "乙"},
    ]
    units = split_parents_and_children(
        blocks, parent_tokens=500, child_tokens=200, overlap_ratio=0.1,
    )
    assert len(units) >= 2
    assert units[-1]["heading_path"] == "乙"
    assert units[-1]["children"] == []  # 乙节父块短，自身即叶子
    # 乙节内容不出现在甲节任何父块/子块中
    for p in units[:-1]:
        assert "乙节内容" not in p["text"]
        for c in p["children"]:
            assert "乙节内容" not in c["text"]
            assert c["heading_path"] == "甲" and c["page_no"] == 1
    # 父块间不重叠（父块层 overlap_ratio=0）：前块尾部不是后块开头
    for prev, nxt in pairwise(units[:-1]):
        assert nxt["text"][:20] != prev["text"][-20:]


def test_child_token_count_independent():
    """子块 token_count 按自身文本单独计算，与父块/兄弟块无关。"""
    long_text = "这是很长的句子。" * 200
    units = split_parents_and_children(
        [{"text": long_text, "page_no": None, "heading_path": "甲"}],
        parent_tokens=500, child_tokens=200, overlap_ratio=0.1,
    )
    for p in units:
        for c in p["children"]:
            assert c["token_count"] == _expected_tokens(c["text"])
