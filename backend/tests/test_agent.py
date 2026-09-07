"""Agent 引擎测试：SSE 事件顺序 / 工具失败降级 / 超轮数收尾 / citations 去重 / SSRF 拒绝。

不触网：模型一律用 TestModel / FunctionModel 注入（agent_stream 的 model 参数）。
"""
import asyncio
import json
from dataclasses import dataclass
from uuid import uuid4

import pytest
from pydantic_ai import ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from sqlalchemy import select

import app.services.agent.runner as runner_mod
from app.core.db import SessionLocal
from app.models.entities import (
    Chunk,
    ChunkEmbedding,
    Conversation,
    Document,
    ProviderConfig,
    Workspace,
)
from app.providers.embedding.fake import FakeEmbedding
from app.services.agent.deps import Deps
from app.services.agent.tools import read_url_impl

QUERY = "退款政策几天可以退款"
REFUND_TEXT = "本店退款政策：签收后 7 天内可申请退款，15 天内可换货。"


def parse_sse(events: list[str]) -> list[dict]:
    return [json.loads(e.removeprefix("data: ").strip()) for e in events]


@pytest.fixture
def seed_agent_data():
    """建工作区 + ready 文档 + chunk/embedding + 会话（复用 test_chat 的思路）。"""
    fake = FakeEmbedding(dim=4)
    qvec = asyncio.run(fake.embed([QUERY]))[0]
    other_text = "另一个工作区的隐私内容：内部机密密码 123456。"
    ovec = asyncio.run(fake.embed([other_text]))[0]

    added_ws: list[int] = []
    with SessionLocal() as s:
        s.add(ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                             is_default=True, params={"dim": 4}))
        s.commit()

        ws = Workspace(name=f"ws-agent-{uuid4()}")
        s.add(ws)
        s.flush()
        added_ws.append(ws.id)
        doc = Document(workspace_id=ws.id, filename="refund.md", source_type="upload",
                       mime="text/markdown", size=10, checksum=f"c-{uuid4()}", status="ready")
        s.add(doc)
        s.flush()
        c1 = Chunk(document_id=doc.id, workspace_id=ws.id, ordinal=0, content=REFUND_TEXT,
                   token_count=10, heading_path="售后/退款", page_no=1)
        s.add(c1)
        s.flush()
        s.add(ChunkEmbedding(chunk_id=c1.id, workspace_id=ws.id, model_name="fake",
                             dim=4, embedding=qvec))
        conv = Conversation(workspace_id=ws.id, title="agent-test")
        s.add(conv)
        s.commit()

        # 另一个工作区的同名 chunk：验证工作区隔离
        ws2 = Workspace(name=f"ws-agent2-{uuid4()}")
        s.add(ws2)
        s.flush()
        added_ws.append(ws2.id)
        doc2 = Document(workspace_id=ws2.id, filename="secret.md", source_type="upload",
                        mime="text/markdown", size=10, checksum=f"c2-{uuid4()}", status="ready")
        s.add(doc2)
        s.flush()
        c2 = Chunk(document_id=doc2.id, workspace_id=ws2.id, ordinal=0, content=other_text,
                   token_count=10, heading_path="机密", page_no=1)
        s.add(c2)
        s.flush()
        s.add(ChunkEmbedding(chunk_id=c2.id, workspace_id=ws2.id, model_name="fake",
                             dim=4, embedding=ovec))
        s.commit()
        yield {"ws_id": ws.id, "ws2_id": ws2.id, "conv_id": conv.id}
        for wid in added_ws:
            s.delete(s.get(Workspace, wid))
        # 清掉本 fixture 建的 fake embedding 默认配置（模型名 fake 便于识别）
        for r in s.execute(select(ProviderConfig).where(
                ProviderConfig.model == "fake")).scalars().all():
            s.delete(r)
        s.commit()


def _history_has_tool_call(messages) -> bool:
    return any(isinstance(p, ToolCallPart) for m in messages for p in getattr(m, "parts", []))


def kb_then_text_model():
    """FunctionModel（流式）：第一轮调 kb_search，之后流式返回最终文本。"""
    async def fn(messages, info):
        if _history_has_tool_call(messages):
            yield "根据资料，"
            yield "7 天内可退款。"
        else:
            yield {1: DeltaToolCall("kb_search", json.dumps({"query": QUERY}, ensure_ascii=False))}
    return FunctionModel(stream_function=fn)


async def test_agent_sse_event_order(seed_agent_data):
    """工具多轮 → SSE 事件顺序：stage → tool_call → tool_result → delta → citations → done。"""
    events = [e async for e in runner_mod.agent_stream(
        seed_agent_data["conv_id"], QUERY, model=kb_then_text_model())]
    parsed = parse_sse(events)
    types = [e["type"] for e in parsed]
    assert types[0] == "stage" and types[1] == "stage"
    assert "agent" in types and "delta" in types
    assert types[-2:] == ["citations", "done"]

    tool_call = next(e for e in parsed if e["type"] == "agent" and e["event"] == "tool_call")
    tool_result = next(e for e in parsed if e["type"] == "agent" and e["event"] == "tool_result")
    assert tool_call["tool"] == "kb_search" and "query" in tool_call["args"]
    assert "退款" in tool_result["preview"]

    done = parsed[-1]
    assert done["type"] == "done" and done["followups"] == []
    citations = parsed[-2]["items"]
    assert citations and citations[0]["filename"] == "refund.md"
    assert citations[0]["n"] == 1 and citations[0]["page_no"] == 1

    # 消息落库：user + assistant（含 citations JSONB）
    from app.models.entities import Message
    with SessionLocal() as s:
        msgs = s.query(Message).filter(
            Message.conversation_id == seed_agent_data["conv_id"]).order_by(Message.id).all()
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert "7 天" in msgs[1].content
        assert msgs[1].citations[0]["filename"] == "refund.md"


async def test_tool_failure_continues(seed_agent_data):
    """工具失败（内网地址被 SSRF 防护拒绝）→ 错误 Observation 作为结果，运行继续到最终回答。"""
    async def fn(messages, info):
        if _history_has_tool_call(messages):
            yield "网页读不到，仅凭已有资料回答。"
        else:
            yield {1: DeltaToolCall("read_url", json.dumps({"url": "http://127.0.0.1:8000/x"}))}
    events = [e async for e in runner_mod.agent_stream(
        seed_agent_data["conv_id"], "帮我读这个网页", model=FunctionModel(stream_function=fn))]
    parsed = parse_sse(events)
    types = [e["type"] for e in parsed]
    assert types[-2:] == ["citations", "done"]
    tool_result = next(e for e in parsed if e["type"] == "agent" and e["event"] == "tool_result")
    assert "无法读取" in tool_result["preview"] or "拒绝" in tool_result["preview"]
    # 最终 delta 仍生成
    assert any(e["type"] == "delta" for e in parsed)


async def test_usage_limit_exceeded_finishes_with_error(seed_agent_data, monkeypatch):
    """超轮数（UsageLimitExceeded）→ error 事件 + citations/done 收尾，不抛出。"""

    @dataclass
    class TinyDeps(Deps):
        # request_limit=1，TestModel 每轮都调工具，第二轮模型请求即超限
        max_turns: int = 0

    monkeypatch.setattr(runner_mod, "Deps", TinyDeps)
    events = [e async for e in runner_mod.agent_stream(
        seed_agent_data["conv_id"], QUERY, model=TestModel())]
    parsed = parse_sse(events)
    errors = [e for e in parsed if e["type"] == "error"]
    assert errors and "上限" in errors[0]["message"]
    assert parsed[-1]["type"] == "done" and parsed[-1]["followups"] == []


async def test_internal_error_not_leaked_to_client(seed_agent_data, monkeypatch):
    """内部异常（含端点等敏感信息）→ error 事件只给固定中文文案，不透传异常文本。"""

    def _boom(workspace_id: int):
        raise RuntimeError("connect to http://10.0.0.1:9999/internal/v1 failed")

    monkeypatch.setattr(runner_mod, "build_agent", _boom)
    events = [e async for e in runner_mod.agent_stream(seed_agent_data["conv_id"], QUERY)]
    parsed = parse_sse(events)
    errors = [e for e in parsed if e["type"] == "error"]
    assert errors and errors[0]["message"] == "生成失败，请稍后重试。"
    # 内部端点信息不得出现在任何下发事件里
    assert "10.0.0.1" not in "".join(events)
    # 仍以 citations + done 正常收尾，不挂死连接
    assert parsed[-2]["type"] == "citations" and parsed[-1]["type"] == "done"


async def test_kb_search_workspace_isolation_and_dedup(seed_agent_data):
    """kb_search 只返回本工作区命中；重复查询 citations 去重且编号从 1 连续。"""
    from app.services.agent.tools import kb_search_impl
    deps = Deps(workspace_id=seed_agent_data["ws_id"])
    text1 = await kb_search_impl(deps, QUERY)
    assert "退款" in text1 and "[1]" in text1
    assert "机密" not in text1 and "secret" not in text1
    # 再次检索同一来源：citations 去重，不重复登记
    await kb_search_impl(deps, "退款")
    assert len(deps.citations) == 1
    assert deps.citations[0]["n"] == 1
    assert deps.citations[0]["filename"] == "refund.md"
    # trace 登记发生在 runner 侧，这里只需保证 citations 结构完整
    assert deps.citations[0]["snippet"].startswith("本店退款政策")


async def test_read_url_rejects_intranet():
    """read_url 对内网地址返回中文拒绝说明（SSRF 防护路径），不抛异常。"""
    out = await read_url_impl("http://127.0.0.1:8000/secret")
    assert out.startswith("无法读取该网页")
    assert "拒绝访问内网" in out or "127.0.0.1" in out


async def test_conversation_not_found():
    """会话不存在 → error 事件。"""
    events = [e async for e in runner_mod.agent_stream(999999999, "hi", model=TestModel())]
    parsed = parse_sse(events)
    assert parsed[0]["type"] == "error" and "会话不存在" in parsed[0]["message"]


async def test_agent_trace_persisted_and_listed(seed_agent_data, client):
    """agent 模式回答落库带 trace（工具时间线），历史消息接口带出 trace 字段。"""
    events = [e async for e in runner_mod.agent_stream(
        seed_agent_data["conv_id"], QUERY, model=kb_then_text_model())]
    parsed = parse_sse(events)
    assert parsed[-1]["type"] == "done"

    from fastapi.testclient import TestClient  # noqa: F401 — client fixture 提供
    resp = client.get(f"/api/conversations/{seed_agent_data['conv_id']}/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assistant = [m for m in msgs if m["role"] == "assistant"]
    assert assistant and assistant[0]["trace"], "assistant 消息应带工具时间线 trace"
    trace = assistant[0]["trace"]
    assert trace[0]["tool"] == "kb_search"
    assert trace[0]["args"] == {"query": QUERY}
    assert trace[0]["preview"]
    # trace 与 citations 写在同一条 assistant 消息上
    assert assistant[0]["citations"][0]["filename"] == "refund.md"


async def test_read_url_no_document_row():
    """read_url 轻量抓取：不再产生 Document 行/落盘文件（MockTransport 模拟外网页面）。"""
    import httpx

    html = ("<html><head><title>政策页</title></head>"
            "<body><main><p>退款政策内容：7 天可退。</p></main></body></html>").encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, headers={"content-type": "text/html"})

    with SessionLocal() as s:
        before = s.execute(select(Document.id)).scalars().all()

    out = await read_url_impl(
        "https://example.com/policy", transport=httpx.MockTransport(handler))
    assert "7 天可退" in out and len(out) <= 2000

    with SessionLocal() as s:
        after = s.execute(select(Document.id)).scalars().all()
    assert after == before, "read_url 不应产生新的 Document 行"
