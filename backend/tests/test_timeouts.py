"""超时兜底测试：流式护栏（首字/空闲/心跳）、检索超时、rerank 降级、kb_search 超时、错误文案映射。"""
import asyncio
from uuid import uuid4

import httpx
import pytest

from app.services.chat import llm_util
from app.services.chat.llm_util import friendly_error, guarded_llm_stream


class SlowFirstLLM:
    """首字前停 1s 的假 LLM（配合缩小后的超时常量触发护栏）。"""

    def __init__(self, first_delay: float = 1.0):
        self.first_delay = first_delay

    async def stream_chat(self, messages, **params):
        await asyncio.sleep(self.first_delay)
        yield "你好"


class NormalLLM:
    async def stream_chat(self, messages, **params):
        yield "你"
        yield "好"


async def test_guarded_stream_normal():
    out = [x async for x in guarded_llm_stream(NormalLLM(), [{"role": "user", "content": "hi"}])]
    assert out == ["你", "好"]


async def test_guarded_stream_first_token_timeout(monkeypatch):
    monkeypatch.setattr(llm_util, "STREAM_FIRST_TIMEOUT", 0.05)
    with pytest.raises(TimeoutError):
        async for _ in guarded_llm_stream(SlowFirstLLM(first_delay=1.0), []):
            pass


async def test_guarded_stream_idle_timeout(monkeypatch):
    monkeypatch.setattr(llm_util, "STREAM_FIRST_TIMEOUT", 1.0)
    monkeypatch.setattr(llm_util, "STREAM_IDLE_TIMEOUT", 0.05)

    class StallMidway:
        async def stream_chat(self, messages, **params):
            yield "你"
            await asyncio.sleep(1.0)
            yield "好"

    with pytest.raises(TimeoutError):
        async for _ in guarded_llm_stream(StallMidway(), []):
            pass


async def test_guarded_stream_heartbeats(monkeypatch):
    monkeypatch.setattr(llm_util, "STREAM_FIRST_TIMEOUT", 1.0)
    monkeypatch.setattr(llm_util, "STREAM_HEARTBEAT_INTERVAL", 0.01)
    out = [x async for x in guarded_llm_stream(SlowFirstLLM(first_delay=0.05), [])]
    assert out[-1] == "你好"
    assert any(x is None for x in out[:-1])  # 等待期产出过心跳


def test_friendly_error_mapping():
    assert friendly_error(httpx.ConnectError("boom")) == "无法连接模型服务，请检查模型配置"
    assert friendly_error(httpx.ConnectTimeout("boom")) == "无法连接模型服务，请检查模型配置"
    assert friendly_error(TimeoutError()) == "模型响应超时，请稍后重试"
    assert friendly_error(RuntimeError("内部细节")) == "服务异常，请稍后重试"


# ---------- 检索超时与 rerank 降级 ----------


async def test_ask_stream_retrieval_timeout(client, monkeypatch):
    from app.services.chat import service as chat_service
    from app.core.db import SessionLocal
    from app.models.entities import (AppConfig, Conversation, ProviderConfig, Workspace)

    async def slow_retrieve(*a, **k):
        await asyncio.sleep(1.0)
        return []

    monkeypatch.setattr(chat_service, "retrieve", slow_retrieve)
    monkeypatch.setattr(chat_service, "RETRIEVE_TIMEOUT", 0.05)
    monkeypatch.setattr(chat_service, "build_llm_provider", lambda cfg: NormalLLM())

    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        llm_cfg = ProviderConfig(kind="llm", provider="fake", base_url="", model="fake",
                                 is_default=True, params={})
        s.add_all([ws, llm_cfg])
        s.commit()
        conv = Conversation(workspace_id=ws.id)
        s.add(conv)
        s.commit()
        wid, cid, llm_id = ws.id, conv.id, llm_cfg.id
    from sqlalchemy import delete

    try:
        with client.stream("POST", f"/api/conversations/{cid}/ask",
                           json={"question": "测试"}) as resp:
            body = b"".join(resp.iter_bytes()).decode()
        assert '"message":"知识库检索超时，请稍后重试"' in body
    finally:
        with SessionLocal() as s:
            if s.get(ProviderConfig, llm_id):
                s.delete(s.get(ProviderConfig, llm_id))
            if s.get(Workspace, wid):
                s.delete(s.get(Workspace, wid))
            s.execute(delete(AppConfig).where(AppConfig.key.like("suggestions:%")))
            s.commit()


async def test_retrieve_rerank_timeout_degrades(monkeypatch):
    from app.services.retrieval import search as search_mod

    class SlowRerank:
        async def rerank(self, query, docs, top_n):
            await asyncio.sleep(1.0)
            return list(range(len(docs)))

    monkeypatch.setattr(search_mod, "RERANK_TIMEOUT", 0.05)
    monkeypatch.setattr(search_mod, "search",
                        lambda *a, **k: _async([{"chunk_id": 1, "document_id": 1,
                                                 "filename": "f", "content": "c",
                                                 "heading_path": "", "page_no": None,
                                                 "score": 0.9}]))
    monkeypatch.setattr(search_mod, "build_rerank_provider", lambda cfg: SlowRerank())

    class _Cfg:
        pass

    async def _default_provider_or_none(s, kind):
        return _Cfg()

    monkeypatch.setattr(search_mod, "default_provider_or_none", _default_provider_or_none)
    hits = await search_mod.retrieve(1, "q", use_rerank=True)
    assert hits and hits[0]["content"] == "c"  # 降级返回未重排结果


async def _async(result):
    return result


# ---------- kb_search 工具超时 ----------


async def test_kb_search_timeout_returns_message(monkeypatch):
    from app.services.agent import tools as agent_tools
    from app.services.agent.deps import Deps

    async def slow_retrieve(*a, **k):
        await asyncio.sleep(1.0)

    monkeypatch.setattr(agent_tools, "retrieve", slow_retrieve)
    monkeypatch.setattr(agent_tools, "KB_SEARCH_TIMEOUT", 0.05)
    deps = Deps(workspace_id=1, citations=[])
    out = await agent_tools.kb_search_impl(deps, "q")
    assert "检索超时" in out
