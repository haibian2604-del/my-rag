"""Agent 工具实现：知识库检索、网页阅读（含 SSRF 防护）。

纯逻辑放在模块级 _impl 函数，便于脱离 Agent 直接单测；
build_agent 内用 @agent.tool 注册薄封装（RunContext 取 deps）。
"""
import logging
from urllib.parse import urljoin

import httpx

from app.services.agent.deps import Deps
from app.services.ingestion.web_fetch import (
    MAX_BODY_BYTES,
    MAX_REDIRECTS,
    TIMEOUT,
    UrlFetchError,
    _parse_page,
    validate_url,
)
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


async def read_url_impl(url: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """轻量抓取网页正文（不落 Document 行/不落盘），截 2000 字符；失败返回中文说明。

    与 fetch_url_to_doc 同样的安全约束：每跳重定向都做 SSRF 校验、限制跳数与响应体大小；
    仅抓取+解析（web_fetch._parse_page），不产生任何入库副作用。
    transport 仅供测试注入（httpx.MockTransport）。
    """
    try:
        validate_url(url)
    except UrlFetchError as e:
        return f"无法读取该网页：{e}"
    try:
        current = url
        async with httpx.AsyncClient(
                follow_redirects=False, timeout=TIMEOUT, transport=transport) as client:
            for _ in range(MAX_REDIRECTS):
                try:
                    resp = await client.get(current)
                except httpx.HTTPError as e:
                    raise UrlFetchError(f"抓取失败: {e}") from e
                if 300 <= resp.status_code < 400:
                    location = resp.headers.get("location")
                    if not location:
                        raise UrlFetchError(f"重定向缺少 Location: {resp.status_code}")
                    current = urljoin(current, location)
                    validate_url(current)  # 每跳重新校验，防止重定向逃逸到内网
                    continue
                if resp.status_code >= 400:
                    raise UrlFetchError(f"抓取失败: HTTP {resp.status_code}")
                if len(resp.content) > MAX_BODY_BYTES:
                    raise UrlFetchError("响应体超过 10MB 限制")
                text = _parse_page(resp.content, resp.headers.get("content-type", ""))
                return text[:TOOL_OUTPUT_LIMIT] or "网页未提取到正文内容。"
            raise UrlFetchError(f"重定向超过 {MAX_REDIRECTS} 跳")
    except Exception as e:
        logger.warning("read_url 抓取失败: %s", url, exc_info=True)
        return f"网页抓取失败：{e}"
