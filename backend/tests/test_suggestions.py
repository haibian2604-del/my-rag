"""建议问题与追问测试：接口四态 / 缓存指纹 / OpenAICompatLLM.complete / SSE done followups。"""
import asyncio
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.models.entities import AppConfig, Chunk, Conversation, Document, ProviderConfig, Workspace
from app.providers.llm.openai_compat import OpenAICompatLLM
from app.services.chat.llm_util import llm_complete, parse_json_list

JSON_REPLY = '["如何申请退款？","发货要多久？","会员积分怎么算？"]'


def _seed(ws_id: int, n_docs: int = 1):
    """插入 ready 文档 + 父块。"""
    with SessionLocal() as s:
        for i in range(n_docs):
            doc = Document(workspace_id=ws_id, filename=f"d{i}.md", source_type="upload",
                           mime="text/markdown", size=10, checksum=f"c-{uuid4()}", status="ready")
            s.add(doc)
            s.flush()
            s.add(Chunk(document_id=doc.id, workspace_id=ws_id, ordinal=0,
                        content=f"文档{i}：签收后 7 天内可申请退款。", token_count=10,
                        heading_path="售后", parent_id=None))
        s.commit()


def _setup_llm(reply: str | None):
    """配置（或删除）fake 默认 LLM。reply=None 表示不配置。"""
    with SessionLocal() as s:
        if reply is None:
            return None
        cfg = ProviderConfig(kind="llm", provider="fake", base_url="", model="fake",
                             is_default=True, params={"reply": reply})
        s.add(cfg)
        s.commit()
        return cfg.id


def _cleanup(ids: list[int], ws_ids: list[int]):
    with SessionLocal() as s:
        for cid in ids:
            if cid and s.get(ProviderConfig, cid):
                s.delete(s.get(ProviderConfig, cid))
        for wid in ws_ids:
            if s.get(Workspace, wid):
                s.delete(s.get(Workspace, wid))
        s.execute(delete(AppConfig).where(AppConfig.key.like("suggestions:%")))
        s.commit()


def test_suggestions_empty_workspace(client):
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        s.add(ws)
        s.commit()
        wid = ws.id
    try:
        resp = client.get(f"/api/workspaces/{wid}/suggestions")
        assert resp.status_code == 200
        assert resp.json() == {"questions": []}
    finally:
        _cleanup([], [wid])


def test_suggestions_generated_and_cached(client):
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        s.add(ws)
        s.commit()
        wid = ws.id
    llm_id = _setup_llm(JSON_REPLY)
    _seed(wid)
    try:
        resp = client.get(f"/api/workspaces/{wid}/suggestions")
        assert resp.status_code == 200
        assert resp.json()["questions"] == ["如何申请退款？", "发货要多久？", "会员积分怎么算？"]
        # 成功结果应写缓存
        with SessionLocal() as s:
            assert s.get(AppConfig, f"suggestions:{wid}") is not None
    finally:
        _cleanup([llm_id], [wid])


def test_suggestions_llm_failure_degrades_empty(client):
    """LLM 返回无法解析的内容 → 降级空数组，且不写缓存。"""
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        s.add(ws)
        s.commit()
        wid = ws.id
    llm_id = _setup_llm("抱歉，我无法生成。")
    _seed(wid)
    try:
        resp = client.get(f"/api/workspaces/{wid}/suggestions")
        assert resp.status_code == 200
        assert resp.json() == {"questions": []}
        with SessionLocal() as s:
            assert s.get(AppConfig, f"suggestions:{wid}") is None
    finally:
        _cleanup([llm_id], [wid])


def test_suggestions_cache_fingerprint_hit(client):
    """缓存指纹命中（文档数量+最大 id 未变）→ 直接返回缓存，不再调 LLM（此处连 LLM 都未配置）。"""
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        s.add(ws)
        s.commit()
        wid = ws.id
        _seed(wid)
        doc_count = s.execute(
            select(func.count()).select_from(Document)
            .where(Document.workspace_id == wid, Document.status == "ready")
        ).scalar_one()
        max_doc_id = s.execute(
            select(func.max(Document.id)).where(Document.workspace_id == wid)
        ).scalar_one()
        s.merge(AppConfig(key=f"suggestions:{wid}", value={
            "fingerprint": f"{doc_count}:{max_doc_id}",
            "questions": ["缓存的问题？"],
        }))
        s.commit()
    try:
        resp = client.get(f"/api/workspaces/{wid}/suggestions")
        assert resp.status_code == 200
        assert resp.json() == {"questions": ["缓存的问题？"]}
    finally:
        _cleanup([], [wid])


# ---------- LLM 非流式工具 ----------


async def test_openai_compat_complete_parses_message_content(httpx_mock):
    httpx_mock.add_response(json={"choices": [{"message": {"content": "你好，世界"}}]})
    llm = OpenAICompatLLM(base_url="http://x/v1", model="m", api_key="sk-t")
    text = await llm.complete([{"role": "user", "content": "hi"}], timeout=5)
    assert text == "你好，世界"
    req = httpx_mock.get_requests()[0]
    assert b'"stream":false' in req.content.replace(b" ", b"")
    assert req.headers["Authorization"] == "Bearer sk-t"


async def test_llm_complete_degrades_to_none_on_error(httpx_mock):
    httpx_mock.add_response(status_code=500, text="boom")
    llm = OpenAICompatLLM(base_url="http://x/v1", model="m")
    assert await llm_complete(llm, [{"role": "user", "content": "hi"}]) is None


def test_parse_json_list_tolerant():
    assert parse_json_list('["a","b"]') == ["a", "b"]
    assert parse_json_list('```json\n["a"]\n```') == ["a"]
    assert parse_json_list('好的：["a","b"] 请查收') == ["a", "b"]
    assert parse_json_list("不是 JSON") == []
    assert parse_json_list(None) == []
    assert parse_json_list('["a", 1, ""]') == ["a"]  # 过滤非字符串与空串


# ---------- SSE done 事件 followups ----------


class FakeLLMWithComplete:
    def __init__(self):
        self.seen = []

    async def stream_chat(self, messages, **params):
        self.seen.append(("stream", messages))
        yield "这是回答。"

    async def complete(self, messages, timeout=None, **params):
        self.seen.append(("complete", messages))
        return '["追问一？","追问二？"]'


def test_ask_done_event_contains_followups(client, monkeypatch):
    import app.services.chat.service as chat_service

    fake = FakeLLMWithComplete()
    monkeypatch.setattr(chat_service, "build_llm_provider", lambda cfg: fake)
    monkeypatch.setattr(chat_service, "retrieve", lambda *a, **k: asyncio.sleep(0, result=[]))

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
    try:
        with client.stream("POST", f"/api/conversations/{cid}/ask",
                           json={"question": "退款政策是什么？"}) as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes()).decode()
        assert '"type":"done"' in body
        assert '"followups":["追问一？","追问二？"]' in body
        # 追问调用应发生在回答生成之后，prompt 含用户问题与回答
        completes = [m for kind, m in fake.seen if kind == "complete"]
        assert completes and "退款政策是什么？" in completes[0][-1]["content"]
    finally:
        _cleanup([llm_id], [wid])
