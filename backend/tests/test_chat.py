"""会话与流式问答测试：SSE 事件序列 / 消息落库 / 历史 / LLM 未配置 400。"""
import asyncio
from typing import ClassVar
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.entities import (
    Chunk,
    ChunkEmbedding,
    Conversation,
    Document,
    Message,
    ProviderConfig,
    Workspace,
)
from app.providers.embedding.fake import FakeEmbedding

QUERY = "退款政策几天可以退款"


class RecordingFakeLLM:
    """记录收到的 messages 的 fake LLM，用于断言 P1（问题不重复）。"""

    instances: ClassVar[list] = []

    def __init__(self, reply: str = "回答。"):
        self.reply = reply
        self.seen_messages = None
        RecordingFakeLLM.instances.append(self)

    async def stream_chat(self, messages: list[dict], **params):
        self.seen_messages = messages
        yield self.reply


@pytest.fixture
def seed_data():
    """复用 test_retrieval 的思路：手工插入 chunk + embedding，保证检索命中。"""
    fake = FakeEmbedding(dim=4)
    refund_text = "本店退款政策：签收后 7 天内可申请退款，15 天内可换货。"
    ship_text = "本店发货时间为工作日 48 小时内，偏远地区除外。"
    qvec = asyncio.run(fake.embed([QUERY]))[0]
    ship_vec = asyncio.run(fake.embed([ship_text]))[0]

    added = {"workspaces": [], "configs": []}
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        emb_cfg = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                                 is_default=True, params={"dim": 4})
        llm_cfg = ProviderConfig(kind="llm", provider="fake", base_url="", model="fake",
                                 is_default=True, params={"reply": "签收后 7 天内可申请退款 [1]。"})
        s.add_all([ws, emb_cfg, llm_cfg])
        s.flush()
        added["workspaces"].append(ws.id)
        added["configs"] = [emb_cfg.id, llm_cfg.id]

        doc = Document(workspace_id=ws.id, filename="refund.md", source_type="upload",
                       mime="text/markdown", size=10, checksum=f"c-{uuid4()}", status="ready")
        s.add(doc)
        s.flush()
        c1 = Chunk(document_id=doc.id, workspace_id=ws.id, ordinal=0, content=refund_text,
                   token_count=10, heading_path="售后/退款", page_no=1)
        c2 = Chunk(document_id=doc.id, workspace_id=ws.id, ordinal=1, content=ship_text,
                   token_count=10, heading_path="发货", page_no=2)
        s.add_all([c1, c2])
        s.flush()
        s.add_all([
            ChunkEmbedding(chunk_id=c1.id, workspace_id=ws.id, model_name="fake",
                           dim=4, embedding=qvec),
            ChunkEmbedding(chunk_id=c2.id, workspace_id=ws.id, model_name="fake",
                           dim=4, embedding=ship_vec),
        ])
        s.commit()
        yield {"ws": ws, "ws_id": ws.id, "llm_cfg_id": llm_cfg.id}
        for w in added["workspaces"]:
            s.delete(s.get(Workspace, w))
        for cid in added["configs"]:
            s.delete(s.get(ProviderConfig, cid))
        s.commit()


def test_create_conversation_201(client, seed_data):
    resp = client.post(f"/api/workspaces/{seed_data['ws_id']}/conversations")
    assert resp.status_code == 201
    body = resp.json()
    assert body["workspace_id"] == seed_data["ws_id"]
    assert body["title"] == "新对话"


def test_ask_streams_citations_and_persists(client, seed_data):
    with SessionLocal() as s:
        conv = Conversation(workspace_id=seed_data["ws_id"])
        s.add(conv)
        s.commit()
        cid = conv.id
    with client.stream("POST", f"/api/conversations/{cid}/ask",
                       json={"question": QUERY}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b"".join(resp.iter_bytes()).decode()
    assert '"type":"citations"' in body and '"type":"delta"' in body and '"type":"done"' in body
    # 事件顺序：stage(retrieving) → stage(generating) → citations → delta → done
    # （未配置默认 rerank provider，不应出现 reranking 阶段）
    seq = ['"stage":"retrieving"', '"stage":"generating"',
           '"type":"citations"', '"type":"delta"', '"type":"done"']
    idx = [body.index(t) for t in seq]
    assert idx == sorted(idx)
    assert '"stage":"reranking"' not in body
    with SessionLocal() as s:
        msgs = s.execute(select(Message).where(Message.conversation_id == cid)
                         .order_by(Message.id)).scalars().all()
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[1].citations and msgs[1].citations[0]["n"] == 1
        assert msgs[1].content.startswith("签收后")
        # 清理
        s.delete(s.get(Conversation, cid))
        s.commit()


def test_ask_emits_reranking_stage_with_rerank_provider(client, seed_data):
    """配置默认 rerank provider 时，事件序列应含 reranking 阶段（调用失败自动降级不阻塞）。"""
    with SessionLocal() as s:
        # 指向不可达地址：stage 应照常发出，rerank 失败走降级，检索结果不受影响
        rr = ProviderConfig(kind="rerank", provider="omlx", base_url="http://127.0.0.1:1",
                            model="fake-rerank", is_default=True)
        s.add(rr)
        s.commit()
        rr_id = rr.id
        conv = Conversation(workspace_id=seed_data["ws_id"])
        s.add(conv)
        s.commit()
        cid = conv.id
    try:
        with client.stream("POST", f"/api/conversations/{cid}/ask",
                           json={"question": QUERY}) as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes()).decode()
        seq = ['"stage":"retrieving"', '"stage":"reranking"', '"stage":"generating"',
               '"type":"citations"', '"type":"delta"', '"type":"done"']
        idx = [body.index(t) for t in seq]
        assert idx == sorted(idx)
    finally:
        with SessionLocal() as s:
            s.delete(s.get(ProviderConfig, rr_id))
            s.delete(s.get(Conversation, cid))
            s.commit()


def test_ask_without_llm_config_returns_400(client, seed_data):
    with SessionLocal() as s:
        llm_cfg = s.get(ProviderConfig, seed_data["llm_cfg_id"])
        s.delete(llm_cfg)
        s.commit()
        conv = Conversation(workspace_id=seed_data["ws_id"])
        s.add(conv)
        s.commit()
        cid = conv.id
    resp = client.post(f"/api/conversations/{cid}/ask", json={"question": QUERY})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "未配置 LLM 模型"
    # 清理会话
    with SessionLocal() as s:
        s.delete(s.get(Conversation, cid))
        s.commit()


def test_history_messages_roundtrip(client, seed_data):
    resp = client.post(f"/api/workspaces/{seed_data['ws_id']}/conversations")
    cid = resp.json()["id"]
    with SessionLocal() as s:
        s.add_all([
            Message(conversation_id=cid, role="user", content="问题一"),
            Message(conversation_id=cid, role="assistant", content="回答一",
                    citations=[{"n": 1, "filename": "a.md", "heading_path": "", "page_no": 1,
                                "snippet": "..."}]),
        ])
        s.commit()
    resp = client.get(f"/api/conversations/{cid}/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["citations"][0]["n"] == 1
    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}/messages").status_code == 404


def test_ask_question_appears_only_once_in_llm_messages(client, seed_data, monkeypatch):
    import app.services.chat.service as chat_service

    RecordingFakeLLM.instances.clear()
    monkeypatch.setattr(chat_service, "build_llm_provider", lambda cfg: RecordingFakeLLM("签收后 7 天内可申请退款 [1]。"))

    with SessionLocal() as s:
        conv = Conversation(workspace_id=seed_data["ws_id"])
        s.add(conv)
        s.commit()
        # 预置历史（不含本问）
        s.add(Message(conversation_id=conv.id, role="user", content="历史问题"))
        s.add(Message(conversation_id=conv.id, role="assistant", content="历史回答"))
        s.commit()
        cid = conv.id
    with client.stream("POST", f"/api/conversations/{cid}/ask", json={"question": QUERY}) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode()
    assert '"type":"done"' in body
    llm = RecordingFakeLLM.instances[-1]
    assert llm.seen_messages is not None
    assert llm.seen_messages[0]["role"] == "system"
    user_msgs = [m for m in llm.seen_messages if m["role"] == "user"]
    assert [m["content"] for m in user_msgs] == ["历史问题", QUERY]  # 本问恰好一次
    with SessionLocal() as s:
        s.delete(s.get(Conversation, cid))
        s.commit()


def test_ask_includes_recent_history_in_prompt(client, seed_data):
    with SessionLocal() as s:
        conv = Conversation(workspace_id=seed_data["ws_id"])
        s.add(conv)
        s.commit()
        cid = conv.id
        # 造 12 条历史，验证仅取最近 10 条
        for i in range(12):
            s.add(Message(conversation_id=cid, role="user", content=f"历史问题 {i}"))
        s.commit()
    from app.services.chat.service import load_history
    with SessionLocal() as s:
        hist = load_history(s, cid)
        assert len(hist) == 10
        assert hist[0]["content"] == "历史问题 2"
        assert hist[-1]["content"] == "历史问题 11"
        s.delete(s.get(Conversation, cid))
        s.commit()


def test_ask_reads_workspace_params(client, seed_data, monkeypatch):
    """工作区 params 应接线到检索与上下文组装（top_k/score_threshold/use_rerank/context_max_tokens）。"""
    import app.services.chat.service as chat_service

    RecordingFakeLLM.instances.clear()
    monkeypatch.setattr(chat_service, "build_llm_provider", lambda cfg: RecordingFakeLLM("回答。"))

    recorded = {}

    async def fake_retrieve(workspace_id, query, use_rerank=None, top_k=5, top_n=3,
                            score_threshold=0.0, hybrid=True):
        recorded.update(workspace_id=workspace_id, query=query, use_rerank=use_rerank,
                        top_k=top_k, score_threshold=score_threshold, hybrid=hybrid)
        return []

    monkeypatch.setattr(chat_service, "retrieve", fake_retrieve)

    with SessionLocal() as s:
        ws = s.get(Workspace, seed_data["ws_id"])
        ws.params = {"top_k": 9, "score_threshold": 0.42, "use_rerank": False,
                     "context_max_tokens": 800}
        s.commit()
        conv = Conversation(workspace_id=ws.id)
        s.add(conv)
        s.commit()
        cid = conv.id
    with client.stream("POST", f"/api/conversations/{cid}/ask", json={"question": QUERY}) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode()
    assert '"type":"done"' in body
    assert recorded["workspace_id"] == seed_data["ws_id"]
    assert recorded["top_k"] == 9
    assert recorded["score_threshold"] == 0.42
    assert recorded["use_rerank"] is False

    # context_max_tokens 接线：build_context 的 max_tokens 默认 3000，这里应取 800
    from app.services.retrieval.context import build_context
    hits = [{"filename": "a.md", "heading_path": "", "page_no": 1,
             "content": "字" * 5000}]
    ctx_small, cit_small = build_context(hits, max_tokens=800)
    assert cit_small and len(ctx_small) <= 800 * 2

    with SessionLocal() as s:
        ws = s.get(Workspace, seed_data["ws_id"])
        ws.params = {}
        s.commit()
        s.delete(s.get(Conversation, cid))
        s.commit()
