"""Agent 工具实现：知识库检索、网页阅读（含 SSRF 防护）。

纯逻辑放在模块级 _impl 函数，便于脱离 Agent 直接单测；
build_agent 内用 @agent.tool 注册薄封装（RunContext 取 deps）。
"""
import logging

from app.services.agent.deps import Deps
from app.services.ingestion.web_fetch import UrlFetchError, fetch_url_to_doc, validate_url
from app.services.retrieval.search import retrieve

logger = logging.getLogger(__name__)

# 工具输出统一截断长度（作为 Observation 回传模型）
TOOL_OUTPUT_LIMIT = 2000


async def kb_search_impl(deps: Deps, query: str) -> str:
    """检索本工作区知识库，拼接命中并登记 citations（去重、按登记顺序编号）。"""
    try:
        hits = await retrieve(deps.workspace_id, query, top_k=5)
    except Exception as e:
        logger.warning("kb_search 检索失败", exc_info=True)
        return f"知识库检索失败：{e}"

    if not hits:
        return "知识库中没有找到与该问题相关的资料。"

    sections: list[str] = []
    for h in hits:
        # citations 去重键：同一来源（文件+标题路径+页码）只登记一次
        key = (h["filename"], h.get("heading_path", ""), h.get("page_no"))
        if not any((c["filename"], c["heading_path"], c["page_no"]) == key
                   for c in deps.citations):
            deps.citations.append({
                "n": len(deps.citations) + 1,
                "filename": h["filename"],
                "heading_path": h.get("heading_path", ""),
                "page_no": h.get("page_no"),
                "snippet": h["content"][:200],
            })
        n = next(c["n"] for c in deps.citations
                 if (c["filename"], c["heading_path"], c["page_no"]) == key)
        sections.append(
            f"[{n}] 文件：{h['filename']}"
            + (f"｜章节：{h['heading_path']}" if h.get("heading_path") else "")
            + (f"｜页码：{h['page_no']}" if h.get("page_no") is not None else "")
            + f"\n{h['content']}"
        )
    text = "\n\n".join(sections)
    return text[:TOOL_OUTPUT_LIMIT]


async def read_url_impl(url: str) -> str:
    """校验 URL（SSRF 防护）后抓取网页正文，截 2000 字符；失败返回中文说明。"""
    try:
        validate_url(url)
    except UrlFetchError as e:
        return f"无法读取该网页：{e}"
    try:
        doc = await fetch_url_to_doc(0, url)
        from app.services.ingestion.pipeline import doc_file_path
        text = doc_file_path(doc).read_text(encoding="utf-8", errors="replace")
        return text[:TOOL_OUTPUT_LIMIT] or "网页未提取到正文内容。"
    except Exception as e:
        logger.warning("read_url 抓取失败: %s", url, exc_info=True)
        return f"网页抓取失败：{e}"
