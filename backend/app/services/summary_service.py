"""文档摘要生成（M5-T2）：非流式 LLM 生成 ≤100 字中文摘要。

调用方：ingest_document（置 ready 后独立调用）与手动摘要接口。
约定：无 LLM / 超时 / 任何失败一律返回 None，documents.summary 保持原值，
绝不影响摄取状态机与接口响应。
"""
import logging

from app.core.db import SessionLocal
from app.models.entities import Document
from app.services.chat.llm_util import llm_complete
from app.services.chat.service import get_llm_or_raise
from app.services.ingestion.parsing import parse_file
from app.services.ingestion.pipeline import doc_file_path

logger = logging.getLogger(__name__)

# 送入 prompt 的正文长度上限（字符）
SUMMARY_SOURCE_CHARS = 2000
# 摘要长度上限（字符）
SUMMARY_MAX_CHARS = 100

SUMMARY_PROMPT = (
    "请根据下面的文档内容生成一段不超过 100 字的中文摘要，"
    "概括文档主题与要点，直接输出摘要文本，不要任何前缀或解释。"
    "\n\n文档标题：{filename}\n标题结构：\n{headings}\n\n正文开头：\n{content}"
)


def _extract_source(doc: Document) -> tuple[str, list[str]]:
    """提取摘要素材：复用解析器取文档前 ~2000 字符正文与标题结构。

    解析失败（如文件缺失/格式异常）返回空串，由调用方降级。
    """
    try:
        blocks = parse_file(doc_file_path(doc), doc.mime)
    except Exception:  # noqa: BLE001 — 读不到原文则放弃生成
        return "", []
    headings: list[str] = []
    parts: list[str] = []
    total = 0
    for b in blocks:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        # 收集 markdown 标题行作为标题结构（最多 10 个）
        for line in text.splitlines():
            if line.startswith("#") and len(headings) < 10:
                headings.append(line.lstrip("#").strip())
        parts.append(text)
        total += len(text)
        if total >= SUMMARY_SOURCE_CHARS:
            break
    return "\n".join(parts), headings


async def generate_document_summary(document_id: int) -> str | None:
    """生成并写回文档摘要；成功返回摘要文本，任何失败返回 None（summary 不变）。"""
    with SessionLocal() as s:
        doc = s.get(Document, document_id)
        if not doc:
            return None
        filename = doc.filename
        try:
            llm = get_llm_or_raise(s)
        except Exception:  # noqa: BLE001 — 未配置 LLM 静默关闭
            return None
    content, headings = _extract_source(doc)
    if not content.strip():
        return None
    text = await llm_complete(llm, [{
        "role": "user",
        "content": SUMMARY_PROMPT.format(
            filename=filename, content=content[:SUMMARY_SOURCE_CHARS],
            headings=" / ".join(headings) or "（无）"),
    }])
    if not text:
        return None
    # 压缩空白并截断到 100 字
    summary = " ".join(text.split())[:SUMMARY_MAX_CHARS].strip()
    if not summary:
        return None
    with SessionLocal() as s:
        doc = s.get(Document, document_id)
        if doc:
            doc.summary = summary
            s.commit()
    return summary
