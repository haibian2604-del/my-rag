# app/services/ingestion/chunking.py
"""结构感知切分：将 Block 列表聚合为受 token 上限约束的 Chunk 列表。

语义：
- 段落优先聚合到 max_tokens；单段超限按句子切；单句仍超限按固定 token 窗口硬切。
- 相邻块重叠：保留前一块尾部约 overlap_ratio 的内容，作为下一块的开头（仅限同节内；
  heading_path/page_no 变化时丢弃，避免跨节泄漏）。
- 空文本 block 跳过，不产出空 chunk。
"""
import re

PUNCT = re.compile(r"(?<=[。！？.!?；;])\s*")


def _tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _windows(text: str, max_tokens: int) -> list[str]:
    win = max_tokens * 2  # token ≈ len(text) // 2
    return [text[i:i + win] for i in range(0, len(text), win)]


def split_blocks(blocks: list[dict], max_tokens: int = 500, overlap_ratio: float = 0.1) -> list[dict]:
    chunks: list[dict] = []
    buf: list[str] = []
    buf_tokens = 0
    pending = ""  # 上一块的尾部重叠文本，作为下一块的开头（仅同节携带）
    meta = {"heading_path": "", "page_no": None}

    def flush(carry_overlap: bool = True) -> None:
        nonlocal buf, buf_tokens, pending
        if not buf:  # 只有 pending 时不再重复发射（其内容已随上一块产出）
            pending = ""
            return
        text = pending + "".join(buf)
        if text.strip():
            chunks.append({"text": text, "heading_path": meta["heading_path"],
                           "page_no": meta["page_no"], "token_count": _tokens(text)})
        # 保留本块尾部作为下一块的重叠起点
        if carry_overlap:
            src = "".join(buf)
            overlap_chars = int(_tokens(src) * overlap_ratio) * 2
            pending = src[-overlap_chars:] if overlap_chars > 0 else ""
        else:
            pending = ""
        buf, buf_tokens = [], 0

    for b in blocks:
        text = b["text"]
        hp, page = b.get("heading_path", ""), b.get("page_no")
        if not text or not text.strip():
            continue
        if _tokens(text) > max_tokens:
            # 超长块：换节先收尾（不携带 overlap），再按句子/窗口硬切
            if buf or pending:
                flush(carry_overlap=False)
            meta = {"heading_path": hp, "page_no": page}
            for sent in PUNCT.split(text):
                if not sent:
                    continue
                parts = _windows(sent, max_tokens) if _tokens(sent) > max_tokens else [sent]
                for part in parts:
                    if buf_tokens + _tokens(part) > max_tokens and buf:
                        flush()
                    buf.append(part)
                    buf_tokens += _tokens(part)
            flush()
            continue
        if (buf or pending) and (meta["heading_path"] != hp or meta["page_no"] != page):
            flush(carry_overlap=False)  # 换节：重叠不跨节携带
        meta = {"heading_path": hp, "page_no": page}
        buf.append(text)
        buf_tokens += _tokens(text)
        if buf_tokens >= max_tokens:
            flush()
    if buf:
        flush()
    return chunks


def split_parents_and_children(
    blocks: list[dict],
    parent_tokens: int = 500,
    child_tokens: int = 200,
    overlap_ratio: float = 0.1,
) -> list[dict]:
    """父子分块：结构感知聚合成父块，父块内部再切子块。

    - 父块：沿用 split_blocks 的结构感知聚合（parent_tokens 上限，父块间不重叠，
      避免父块全文在多个引用中重复出现）。每节的 heading_path/page_no 连续，
      故父块必属单节。
    - 子块：父块内部用 split_blocks 再切（同节内轻重叠 overlap_ratio，跨节天然
      不重叠——父块本身不跨节），子块继承父块的 heading_path/page_no，token_count
      单独计算。
    - 父块内容 ≤ child_tokens 时不切子块（children 为空列表），父块自身即叶子，
      直接作为检索单元，行为与旧版切分一致。
    """
    parents = split_blocks(blocks, max_tokens=parent_tokens, overlap_ratio=0.0)
    units: list[dict] = []
    for p in parents:
        if p["token_count"] <= child_tokens:
            children: list[dict] = []  # 短父块不切子块，父块自身即叶子
        else:
            # 父块单节，子块重叠只发生在同节内
            children = split_blocks(
                [{"text": p["text"], "heading_path": p["heading_path"],
                  "page_no": p["page_no"]}],
                max_tokens=child_tokens, overlap_ratio=overlap_ratio,
            )
        units.append({**p, "children": children})
    return units
