"""建议问题与追问生成：非流式 LLM 一次性生成，失败/超时/无 LLM 一律降级为空列表。"""
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.entities import AppConfig, Chunk, Document
from app.services.chat.llm_util import llm_complete, parse_json_list
from app.services.chat.service import get_llm_or_raise

MAX_SUGGESTIONS = 3
MAX_SAMPLE_CHUNKS = 5

SUGGESTIONS_PROMPT = (
    "以下是一份知识库中的若干资料片段。请根据资料内容提出 3 个用户可能想问的问题，"
    "只输出一个 JSON 字符串数组，不要输出其他内容。例如：[\"问题一？\",\"问题二？\",\"问题三？\"]"
    "\n\n资料片段：\n{context}"
)

FOLLOWUPS_PROMPT = (
    "根据下面的用户问题与助手回答，提出 3 个用户可能想继续追问的问题，"
    "只输出一个 JSON 字符串数组，不要输出其他内容。例如：[\"问题一？\",\"问题二？\",\"问题三？\"]"
    "\n\n用户问题：{question}\n\n助手回答：{answer}"
)

# 回答传给追问 prompt 时的截断长度（避免 prompt 过长）
FOLLOWUP_ANSWER_CHARS = 1500


async def get_suggestions(ws_id: int) -> list[str]:
    """工作区建议问题：无文档 → 空；有缓存且指纹命中 → 直接返回；否则采样父块调 LLM 生成。

    任何失败（无 LLM、超时、解析失败）都降级为空列表；仅成功结果写缓存。
    """
    with SessionLocal() as s:
        doc_count = s.execute(
            select(func.count()).select_from(Document)
            .where(Document.workspace_id == ws_id, Document.status == "ready")
        ).scalar_one()
        if doc_count == 0:
            return []
        max_doc_id = s.execute(
            select(func.max(Document.id)).where(Document.workspace_id == ws_id)
        ).scalar_one() or 0
        # 缓存指纹：ready 文档数量 + 最大文档 id，文档增删即失效
        fp = f"{doc_count}:{max_doc_id}"
        cached = s.get(AppConfig, f"suggestions:{ws_id}")
        if cached and (cached.value or {}).get("fingerprint") == fp:
            return list((cached.value or {}).get("questions", []))
        # 采样父块（无父块的叶子兼容旧行为）片段作为生成素材
        contents = s.execute(
            select(Chunk.content)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.workspace_id == ws_id, Document.status == "ready",
                   Chunk.parent_id.is_(None))
            .order_by(Chunk.id)
            .limit(MAX_SAMPLE_CHUNKS)
        ).scalars().all()
        if not contents:
            return []
        try:
            llm = get_llm_or_raise(s)
        except Exception:  # noqa: BLE001 — 未配置 LLM 静默关闭
            return []
    context = "\n\n".join(c[:500] for c in contents)
    text = await llm_complete(
        llm, [{"role": "user", "content": SUGGESTIONS_PROMPT.format(context=context)}],
    )
    questions = parse_json_list(text)[:MAX_SUGGESTIONS]
    if not questions:
        return []
    with SessionLocal() as s:
        s.merge(AppConfig(
            key=f"suggestions:{ws_id}",
            value={"fingerprint": fp, "questions": questions},
        ))
        s.commit()
    return questions


async def generate_followups(question: str, answer: str, llm=None) -> list[str]:
    """根据本轮问答生成追问建议；任何失败降级为空列表（绝不阻塞问答流）。

    llm 可传入问答流已构造好的 provider，避免重复构造与配置读取。
    """
    try:
        if llm is None:
            with SessionLocal() as s:
                llm = get_llm_or_raise(s)
        text = await llm_complete(llm, [{
            "role": "user",
            "content": FOLLOWUPS_PROMPT.format(
                question=question[:500], answer=answer[:FOLLOWUP_ANSWER_CHARS]),
        }])
        return parse_json_list(text)[:MAX_SUGGESTIONS]
    except Exception:  # noqa: BLE001 — 生成失败不显示追问
        return []
