"""会话问答服务：检索 → 组装 messages → 流式生成 → 落库。"""
import json
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.entities import Conversation, Message, ProviderConfig, Workspace
from app.providers.llm.fake import FakeLLM
from app.providers.llm.openai_compat import OpenAICompatLLM
from app.services.ingestion.pipeline import get_default_provider
from app.services.providers_service import decrypt_api_key
from app.services.retrieval.context import build_context
from app.services.retrieval.search import retrieve

SYSTEM_PROMPT = (
    "你是个人知识库助手。仅依据提供的资料回答；"
    "引用资料时在句末标注 [n]；资料不足以回答时明确说明。"
)
HISTORY_LIMIT = 10


class LLMNotConfiguredError(RuntimeError):
    pass


def build_llm_provider(cfg: ProviderConfig):
    if cfg.provider == "fake":
        params = cfg.params or {}
        return FakeLLM(reply=params.get("reply", "这是一个测试回答。"))
    params = cfg.params or {}
    return OpenAICompatLLM(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=decrypt_api_key(cfg),
        timeout=params.get("timeout", 120.0),
    )


def get_llm_or_raise(s: Session):
    try:
        cfg = get_default_provider(s, "llm")
    except RuntimeError as e:
        raise LLMNotConfiguredError("未配置 LLM 模型") from e
    return build_llm_provider(cfg)


def load_history(s: Session, conversation_id: int) -> list[dict]:
    rows = s.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(HISTORY_LIMIT)
    ).scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def ask_stream(conversation_id: int, question: str) -> AsyncIterator[str]:
    """SSE 生成器：citations → delta* → done；异常发 error 事件后结束。"""
    with SessionLocal() as s:
        conv = s.get(Conversation, conversation_id)
        if not conv:
            yield _sse({"type": "error", "message": "会话不存在"})
            return
        try:
            llm = get_llm_or_raise(s)
            ws = s.get(Workspace, conv.workspace_id)
            ws_params = dict(ws.params or {}) if ws else {}
            top_k = int(ws_params.get("top_k", 5))
            score_threshold = float(ws_params.get("score_threshold", 0.0))
            _rerank = ws_params.get("use_rerank")
            use_rerank = None if _rerank is None else bool(_rerank)
            max_tokens = int(ws_params.get("context_max_tokens", 3000))
            hits = await retrieve(conv.workspace_id, question, use_rerank=use_rerank,
                                  top_k=top_k, score_threshold=score_threshold)
            ctx, citations = build_context(hits, max_tokens=max_tokens)
            # 先取历史（不含本问），再落库 user 消息，避免历史里混入刚写入的问题
            history = load_history(s, conversation_id)
            s.add(Message(conversation_id=conversation_id, role="user", content=question))
            s.commit()
            messages = (
                [{"role": "system", "content": SYSTEM_PROMPT + (f"\n\n资料：\n{ctx}" if ctx else "")}]
                + history
                + [{"role": "user", "content": question}]
            )
            yield _sse({"type": "citations", "items": citations})

            parts: list[str] = []
            async for delta in llm.stream_chat(messages):
                parts.append(delta)
                yield _sse({"type": "delta", "text": delta})

            s.add(Message(
                conversation_id=conversation_id,
                role="assistant",
                content="".join(parts),
                citations=citations or None,
            ))
            s.commit()
            yield _sse({"type": "done"})
        except LLMNotConfiguredError:
            yield _sse({"type": "error", "message": "未配置 LLM 模型"})
        except Exception as e:  # noqa: BLE001 — 流中异常以 error 事件告知前端
            yield _sse({"type": "error", "message": str(e)})
