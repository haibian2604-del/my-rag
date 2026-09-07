"""Agent 流式运行器：消费 PydanticAI 事件 → SSE（与 RAG 模式同一事件方言）。

事件序列：stage(retrieving) → stage(generating) → agent(tool_call/tool_result)*
→ delta* → citations → done(followups=[])；超轮数/超时等异常降级为 error 事件
+ citations + done 收尾，绝不抛出或挂死连接。
"""
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallEvent,
    ToolCallPart,
    ToolResultEvent,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from app.core.db import SessionLocal
from app.models.entities import Conversation, Message
from app.services.agent.agent import build_agent
from app.services.agent.deps import Deps
from app.services.chat.service import load_history

# Agent 整体运行时限（秒）
AGENT_TIMEOUT = 120.0

# 工具结果作为 Observation 的截断长度（与工具实现侧一致）
_TOOL_PREVIEW_LIMIT = 200


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _preview(value: Any, limit: int = _TOOL_PREVIEW_LIMIT) -> str:
    """任意工具参数/结果转短预览文本（默认截 200 字符）。"""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    return text[:limit]


def _history_to_messages(history: list[dict]) -> list:
    """把 RAG 风格的 [{role, content}] 历史转换为 PydanticAI 消息对象。"""
    from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
    msgs: list = []
    for m in history:
        if m["role"] == "user":
            msgs.append(ModelRequest([UserPromptPart(content=m["content"])]))
        elif m["role"] == "assistant":
            msgs.append(ModelResponse([TextPart(content=m["content"])]))
    return msgs


def _part_args(part: ToolCallPart) -> Any:
    """ToolCallPart.args 可能是 dict 或 JSON 字符串，统一还原成 JSON 兼容值。"""
    args = part.args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except ValueError:
            return args
    return args


async def agent_stream(
    conversation_id: int, question: str, model: Model | None = None
) -> AsyncIterator[str]:
    """Agent 模式 SSE 生成器（T2 按 mode 分流调用；model 仅测试注入 TestModel 用）。"""
    with SessionLocal() as s:
        conv = s.get(Conversation, conversation_id)
        if not conv:
            yield _sse({"type": "error", "message": "会话不存在"})
            return
        workspace_id = conv.workspace_id
        # 历史加载与 user 消息落库：与 RAG 相同的模式（先取历史再写 user，
        # 避免历史里混入刚写入的问题）
        history = load_history(s, conversation_id)
        s.add(Message(conversation_id=conversation_id, role="user", content=question))
        s.commit()

        agent = build_agent(workspace_id)
        deps = Deps(workspace_id=workspace_id)
        yield _sse({"type": "stage", "stage": "retrieving"})
        yield _sse({"type": "stage", "stage": "generating"})

        parts: list[str] = []
        error: str | None = None
        # 记录最近一次工具调用参数，结果事件到达时与 preview 一起写入 trace
        last_args: dict[str, Any] = {}
        try:
            async with asyncio.timeout(AGENT_TIMEOUT):
                async with agent.iter(
                    question,
                    deps=deps,
                    model=model,
                    message_history=_history_to_messages(history),
                    usage_limits=UsageLimits(request_limit=deps.max_turns + 1),
                ) as run:
                    async for node in run:
                        if Agent.is_model_request_node(node):
                            # 模型流式输出：TextPart 增量 → delta 事件
                            async with node.stream(run.ctx) as stream:
                                async for ev in stream:
                                    # 文本块可能以 PartStartEvent（首个块）或
                                    # PartDeltaEvent（后续块）两种形态到达
                                    text: str | None = None
                                    if isinstance(ev, PartDeltaEvent) and isinstance(
                                        ev.delta, TextPartDelta
                                    ):
                                        text = ev.delta.content_delta
                                    elif isinstance(ev, PartStartEvent) and isinstance(
                                        ev.part, TextPart
                                    ):
                                        text = ev.part.content
                                    if text:
                                        parts.append(text)
                                        yield _sse({"type": "delta", "text": text})
                        elif Agent.is_call_tools_node(node):
                            # 工具执行节点：调用/返回事件 → agent 事件
                            async with node.stream(run.ctx) as stream:
                                async for ev in stream:
                                    if isinstance(ev, ToolCallEvent):
                                        part = ev.part
                                        name = getattr(part, "tool_name", "unknown")
                                        args = _part_args(part) if isinstance(part, ToolCallPart) else None
                                        last_args[name] = args
                                        yield _sse({
                                            "type": "agent", "event": "tool_call",
                                            "tool": name, "args": args,
                                            "preview": _preview(args),
                                        })
                                    elif isinstance(ev, ToolResultEvent):
                                        result = ev.part
                                        name = getattr(result, "tool_name", "unknown")
                                        if isinstance(result, ToolReturnPart):
                                            output = result.content
                                        elif isinstance(result, RetryPromptPart):
                                            # 工具重试提示（含工具内未捕获异常）：作为错误 Observation 预览
                                            output = f"工具执行异常：{result.content}"
                                        else:
                                            output = ""
                                        deps.trace.append({
                                            "tool": name, "args": last_args.get(name),
                                            "preview": _preview(output),
                                        })
                                        yield _sse({
                                            "type": "agent", "event": "tool_result",
                                            "tool": name,
                                            "preview": _preview(output),
                                        })
        except UsageLimitExceeded:
            error = "已达本轮工具调用次数上限，请稍后重试或换个问法。"
        except TimeoutError:
            error = "回答超时，已停止生成。"
        except Exception as e:  # noqa: BLE001 — 流中异常以 error 事件降级，绝不抛出
            error = f"生成失败：{e}"

        if error:
            yield _sse({"type": "error", "message": error})
        # 收尾：引用 + done（answer 部分内容可能不完整，仍正常落库供历史展示）
        content = "".join(parts)
        if content:
            s.add(Message(
                conversation_id=conversation_id,
                role="assistant",
                content=content,
                citations=deps.citations or None,
            ))
            s.commit()
        yield _sse({"type": "citations", "items": deps.citations})
        yield _sse({"type": "done", "followups": []})
