# app/services/ingestion/chunking.py
"""结构感知切分：将 Block 列表聚合为受 token 上限约束的 Chunk 列表。"""
import re

PUNCT = re.compile(r"(?<=[。！？.!?；;])\s*")


def _tokens(text: str) -> int:
    return max(1, len(text) // 2)


def split_blocks(blocks: list[dict], max_tokens: int = 500, overlap_ratio: float = 0.1) -> list[dict]:
    chunks: list[dict] = []
    buf: list[str] = []
    buf_tokens = 0
    meta = {"heading_path": "", "page_no": None}

    def flush() -> None:
        nonlocal buf, buf_tokens
        if buf:
            text = "".join(buf)
            chunks.append({"text": text, "heading_path": meta["heading_path"],
                           "page_no": meta["page_no"], "token_count": _tokens(text)})
            # 保留尾部片段作为下一块的重叠
            overlap_len = int(buf_tokens * overlap_ratio)
            kept: list[str] = []
            kept_tokens = 0
            while buf and kept_tokens < overlap_len:
                piece = buf.pop(0)
                kept.append(piece)
                kept_tokens += _tokens(piece)
            buf = kept
            buf_tokens = kept_tokens
        else:
            buf_tokens = 0

    def emit_sentence(text: str, hp: str, page: object) -> None:
        """按句子加入缓冲，必要时 flush（保底：单句超限直接整句成块）。"""
        nonlocal buf, buf_tokens, meta
        if buf_tokens + _tokens(text) > max_tokens and buf:
            flush()
            meta = {"heading_path": hp, "page_no": page}
        buf.append(text)
        buf_tokens += _tokens(text)

    for b in blocks:
        text, hp, page = b["text"], b["heading_path"], b["page_no"]
        if _tokens(text) > max_tokens:
            flush()
            meta = {"heading_path": hp, "page_no": page}
            for sent in PUNCT.split(text):
                if sent:
                    emit_sentence(sent, hp, page)
            flush()
            continue
        if buf and (meta["heading_path"] != hp or meta["page_no"] != page):
            flush()
        meta = {"heading_path": hp, "page_no": page}
        buf.append(text)
        buf_tokens += _tokens(text)
        if buf_tokens >= max_tokens:
            flush()
    flush()
    return chunks
