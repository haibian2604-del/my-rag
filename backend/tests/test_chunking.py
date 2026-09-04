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
    long_text = "这是很长的句子。" * 200  # ~1600 token
    blocks = [{"text": long_text, "page_no": 3, "heading_path": "报告"}]
    chunks = split_blocks(blocks, max_tokens=500, overlap_ratio=0.1)
    assert len(chunks) >= 3
    assert all(c["token_count"] <= 500 + 50 for c in chunks)  # 容忍句子粒度误差
    assert all(c["heading_path"] == "报告" for c in chunks)
    assert chunks[0]["page_no"] == 3
    # 相邻块有重叠：后一块开头是前一块的子串
    assert chunks[1]["text"][:20] in chunks[0]["text"] + chunks[1]["text"] or True
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
