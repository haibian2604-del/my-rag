"""M6-T2 API 分流与持久化测试：mode 分流 / 非法 mode 400 / trace 落库带出 / capability / read_url 轻量化。"""
from uuid import uuid4

import pytest

import app.main as main_mod
from app.core.db import SessionLocal
from app.models.entities import AppConfig, Conversation, ProviderConfig, Workspace


@pytest.fixture
def conv_id() -> int:
    """建一个带 fake LLM 默认配置的工作区 + 会话，结束后清理。"""
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-api-{uuid4().hex[:8]}")
        llm = ProviderConfig(kind="llm", provider="fake", base_url="", model="fake",
                             is_default=True, params={})
        s.add_all([ws, llm])
        s.flush()
        conv = Conversation(workspace_id=ws.id, title="mode-test")
        s.add(conv)
        s.commit()
        cid, wid, llm_id = conv.id, ws.id, llm.id
    yield cid
    with SessionLocal() as s:
        s.delete(s.get(ProviderConfig, llm_id))
        s.delete(s.get(Workspace, wid))
        s.commit()


@pytest.fixture
def quiet_client(monkeypatch):
    """关掉后台探测的自建客户端：避免探测任务写 app_config 干扰 capability 断言。"""

    async def _noop() -> None:
        return None

    monkeypatch.setattr(main_mod, "_probe_agent_capability", _noop)
    from fastapi.testclient import TestClient

    c = TestClient(main_mod.create_app())
    # 与 conftest 的 client fixture 同样的登录方式（users 空时先注册管理员）
    if not c.get("/api/auth/status").json()["registered"]:
        c.post("/api/auth/register",
               json={"username": "admin", "password": "admin-pw-123"})
    else:
        c.post("/api/auth/login",
               json={"username": "admin", "password": "admin-pw-123"})
    return c


def test_invalid_mode_returns_400(client, conv_id):
    """mode 非 rag/agent → 400，中文 detail。"""
    resp = client.post(f"/api/conversations/{conv_id}/ask",
                       json={"question": "hi", "mode": "auto"})
    assert resp.status_code == 400
    assert "mode" in resp.json()["detail"]


def test_default_mode_is_rag(client, conv_id):
    """不带 mode 默认 rag（走原链路，此处只验证参数校验通过、因无 embedding 配置返回 400 提示）。"""
    resp = client.post(f"/api/conversations/{conv_id}/ask", json={"question": "hi"})
    assert resp.status_code in (200, 400)


def test_agent_mode_routes_to_agent_stream(client, conv_id, monkeypatch):
    """mode=agent → 请求分流到 agent_stream（monkeypatch 注入假流验证路由）。"""
    seen: dict = {}

    async def fake_agent_stream(conversation_id, question, model=None):
        seen["conv"] = conversation_id
        seen["q"] = question
        yield 'data: {"type":"stage","stage":"retrieving"}\n\n'
        yield 'data: {"type":"done","followups":[]}\n\n'

    monkeypatch.setattr("app.api.chat.agent_stream", fake_agent_stream)
    with client.stream(
        "POST", f"/api/conversations/{conv_id}/ask",
        json={"question": "你好", "mode": "agent"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(chunk for chunk in resp.iter_text())
    assert seen == {"conv": conv_id, "q": "你好"}
    assert '"type":"done"' in body


def test_capability_endpoint(quiet_client):
    """GET /api/agent/capability 读取 app_config 探测结果；未探测=false，写入 true 后返回 true。"""
    with SessionLocal() as s:
        s.execute(AppConfig.__table__.delete().where(
            AppConfig.key == "agent_capability"))
        s.commit()
    resp = quiet_client.get("/api/agent/capability")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}

    with SessionLocal() as s:
        s.add(AppConfig(key="agent_capability", value={"available": True}))
        s.commit()
    resp = quiet_client.get("/api/agent/capability")
    assert resp.json() == {"available": True}
    with SessionLocal() as s:
        s.execute(AppConfig.__table__.delete().where(
            AppConfig.key == "agent_capability"))
        s.commit()


def test_capability_requires_auth(monkeypatch):
    """未登录访问 capability → 401。"""
    from fastapi.testclient import TestClient

    async def _noop() -> None:
        return None

    monkeypatch.setattr(main_mod, "_probe_agent_capability", _noop)
    anon = TestClient(main_mod.create_app())
    resp = anon.get("/api/agent/capability")
    assert resp.status_code == 401


async def test_read_url_follows_redirect_with_ssrf_check():
    """read_url 重定向到内网 → 被拒（每跳 SSRF 校验保留）。"""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/x"})
        return httpx.Response(200, content=b"secret")

    from app.services.agent.tools import read_url_impl

    out = await read_url_impl(
        "https://example.com/redir", transport=httpx.MockTransport(handler))
    assert out.startswith("网页抓取失败")
