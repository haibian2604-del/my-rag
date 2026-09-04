"""上下文组装：将 Hit 列表拼为带 [n] 标号的上下文文本与 citations 列表。"""

_SNIPPET_CHARS = 200
_APPROX_CHARS_PER_TOKEN = 2  # 中文粗略估计：1 token ≈ 2 字符


def _snippet(content: str, limit: int = _SNIPPET_CHARS) -> str:
    content = content.strip().replace("\n", " ")
    return content[:limit]


def build_context(hits: list[dict], max_tokens: int = 3000) -> tuple[str, list[dict]]:
    max_chars = max_tokens * _APPROX_CHARS_PER_TOKEN
    parts: list[str] = []
    citations: list[dict] = []
    used = 0
    for i, h in enumerate(hits, start=1):
        snippet = _snippet(h["content"])
        block = (
            f"[{i}] 来源：{h['filename']}"
            f"｜标题路径：{h.get('heading_path') or '-'}"
            f"｜页码：{h.get('page_no') if h.get('page_no') is not None else '-'}\n"
            f"{snippet}"
        )
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block)
        citations.append({
            "n": i,
            "filename": h["filename"],
            "heading_path": h.get("heading_path") or "",
            "page_no": h.get("page_no"),
            "snippet": snippet,
        })
    return "\n\n".join(parts), citations
