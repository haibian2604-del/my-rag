from app.services.ingestion.chunking import split_blocks


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
    for prev, nxt in zip(chunks, chunks[1:]):
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
    assert len(set(c["text"] for c in chunks)) == len(chunks)
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
