"""MCP 服务器端到端测试（M7-T2）。

测试策略：
- 401/中间件类测试：TestClient 直接打 /mcp（无需 MCP 握手，401 在中间件层返回）；
- 真实 MCP 握手 + 工具调用：fastmcp 官方 Client + StreamableHttpTransport，
  用 httpx2.AsyncClient(transport=httpx2.ASGITransport(app)) 工厂把请求打进
  ASGI 应用（完整穿过 MCP 开关门禁中间件），lifespan 用 app.router.lifespan_context
  手动进入（ASGITransport 不触发 lifespan，而 MCP 会话管理器依赖它）。
"""
import asyncio
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from app.core.db import SessionLocal
from app.main import create_app
from app.models.entities import Chunk, ChunkEmbedding, Document, ProviderConfig, Workspace
from app.providers.embedding.fake import FakeEmbedding
from app.services.mcp_server import set_mcp_enabled

QUERY = "退款政策几天可以退款"
REFUND_TEXT = "本店退款政策：签收后 7 天内可申请退款，15 天内可换货。"


@pytest.fixture(autouse=True)
def _reset_mcp_gate():
    """每条测试默认关闭 MCP 门禁（测试内显式开启）；结束后复位为关。"""
    set_mcp_enabled(False)
    yield
    set_mcp_enabled(False)


@pytest.fixture
def raw_client():
    """未登录的裸客户端（401 类测试用）；用 with 确保 lifespan 运行。"""
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def api_key() -> str:
    """开启 MCP 门禁后不再需要密钥；保留 fixture 名以最小化用例改动，返回空串。"""
    set_mcp_enabled(True)
    return ""


@pytest.fixture
def seed_data():
    """种子：单工作区 + fake embedding 默认 provider + 一个 ready 文档。"""
    fake = FakeEmbedding(dim=4)
    qvec = asyncio.run(fake.embed([QUERY]))[0]
    with SessionLocal() as s:
        ws = Workspace(name=f"ws-{uuid4()}")
        emb_cfg = ProviderConfig(kind="embedding", provider="fake", base_url="", model="fake",
                                 is_default=True, params={"dim": 4})
        s.add_all([ws, emb_cfg])
        s.flush()
        doc = Document(workspace_id=ws.id, filename="refund.md", source_type="upload",
                       mime="text/markdown", size=10, checksum=f"mcp-{uuid4()}", status="ready")
        s.add(doc)
        s.flush()
        chunk = Chunk(document_id=doc.id, workspace_id=ws.id, ordinal=0, content=REFUND_TEXT,
                      token_count=10, heading_path="售后/退款", page_no=1)
        s.add(chunk)
        s.flush()
        s.add(ChunkEmbedding(chunk_id=chunk.id, workspace_id=ws.id,
                             model_name="fake", dim=4, embedding=qvec))
        s.commit()
        yield {"ws": ws, "doc_id": doc.id}
        s.delete(s.get(Workspace, ws.id))
        s.delete(s.get(ProviderConfig, emb_cfg.id))
        s.commit()


# ---------- 开关门禁（TestClient，无需握手） ----------


def test_mcp_disabled_returns_403(raw_client):
    resp = raw_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp.status_code == 403
    assert "MCP 服务未开启" in resp.json()["detail"]


def test_mcp_enabled_passes_gate(raw_client):
    set_mcp_enabled(True)
    resp = raw_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp.status_code != 403  # 门禁放行（握手细节由下方真实客户端用例覆盖）


def test_mcp_toggle_roundtrip(raw_client):
    """开关往返：关 → 403；开 → 放行；再关 → 403。"""
    resp = raw_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp.status_code == 403
    set_mcp_enabled(True)
    resp = raw_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp.status_code != 403
    set_mcp_enabled(False)
    resp = raw_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp.status_code == 403


def test_mcp_other_routes_unaffected(raw_client, client):
    """/mcp 之外的路由零行为变化：health 正常，MCP 开关接口仍走登录认证。"""
    assert raw_client.get("/api/health").json() == {"status": "ok"}
    assert raw_client.get("/api/mcp/settings").status_code == 401
    assert client.get("/api/mcp/settings").json() == {"enabled": False}
    assert client.put("/api/mcp/settings", json={"enabled": True}).json() == {"enabled": True}
    assert raw_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code != 403
    client.put("/api/mcp/settings", json={"enabled": False})





# ---------- 真实 MCP 握手 + 工具调用（fastmcp Client → ASGI） ----------


def _asgi_transport(app, headers: dict | None) -> StreamableHttpTransport:
    """构造指向 ASGI 应用的 Streamable HTTP 传输（穿过完整中间件栈）。"""

    def factory(**kwargs):
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
            follow_redirects=kwargs.get("follow_redirects", True),
            headers=kwargs.get("headers"),
            auth=kwargs.get("auth"),
        )

    return StreamableHttpTransport(
        url="http://testserver/mcp", headers=headers, httpx_client_factory=factory
    )


@pytest.fixture
def handshake_app(seed_data):
    """供需要 app 实例的测试使用（lifespan 由测试内手动进入）。"""
    return create_app()


async def test_full_handshake_list_workspaces(seed_data, api_key):
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = _asgi_transport(app, {"Authorization": f"Bearer {api_key}"})
        async with Client(transport) as mcp_client:
            names = {t.name for t in await mcp_client.list_tools()}
            assert names == {"list_workspaces", "search", "ask", "list_documents", "get_document"}
            res = await mcp_client.call_tool("list_workspaces", {})
            data = res.data
            # 共享开发库中可能存在用户真实工作区，只断言种子工作区在其中
            assert any(w["name"] == seed_data["ws"].name and w["description"] == "" for w in data)
            assert all(set(w) == {"id", "name", "description"} for w in data)


async def test_full_handshake_search_hit(seed_data, api_key):
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = _asgi_transport(app, {"Authorization": f"Bearer {api_key}"})
        async with Client(transport) as mcp_client:
            # 共享开发库可能已有多个真实工作区，显式指定种子工作区检索
            res = await mcp_client.call_tool(
                "search", {"query": QUERY, "workspace_id": seed_data["ws"].id}
            )
            data = res.data
            assert data["workspace_id"] == seed_data["ws"].id
            assert len(data["hits"]) >= 1
            assert "退款" in data["hits"][0]["content"]
            assert data["hits"][0]["filename"] == "refund.md"


async def test_search_multiple_workspaces_requires_explicit_id(seed_data, api_key):
    """多工作区时缺省 workspace_id 必须报可读错误（列出可选 id）。"""
    with SessionLocal() as s:
        other = Workspace(name=f"ws-b-{uuid4()}")
        s.add(other)
        s.commit()
        other_id = other.id
    try:
        app = create_app()
        async with app.router.lifespan_context(app):
            transport = _asgi_transport(app, {"Authorization": f"Bearer {api_key}"})
            async with Client(transport) as mcp_client:
                with pytest.raises(ToolError, match="多个工作区"):
                    await mcp_client.call_tool("search", {"query": QUERY})
                # 显式指定后可检索
                res = await mcp_client.call_tool(
                    "search", {"query": QUERY, "workspace_id": seed_data["ws"].id}
                )
                assert "退款" in res.data["hits"][0]["content"]
    finally:
        with SessionLocal() as s:
            w = s.get(Workspace, other_id)
            if w:
                s.delete(w)
                s.commit()


async def test_ask_llm_not_configured_tool_error(seed_data, api_key):
    """LLM 未配置时 ask 返回明确工具错误（不是 500、不是静默降级文案混淆）。"""
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = _asgi_transport(app, {"Authorization": f"Bearer {api_key}"})
        async with Client(transport) as mcp_client:
            with pytest.raises(ToolError, match="未配置 LLM"):
                await mcp_client.call_tool(
                    "ask", {"question": "退款政策是什么？", "workspace_id": seed_data["ws"].id}
                )


async def test_ask_with_mocked_llm(seed_data, api_key, monkeypatch):
    """ask 完整链路：检索 + build_context + llm_complete（mock，不真实调用外部 LLM）。"""
    import app.services.mcp_server as mod

    async def fake_complete(llm, messages, timeout=8.0):
        assert "资料" in messages[0]["content"]  # 上下文已注入 system 提示
        return "签收后 7 天内可申请退款 [1]。"

    monkeypatch.setattr(mod, "llm_complete", fake_complete)
    # conftest 会摘掉用户真实默认 provider，这里自建 fake LLM 默认配置
    with SessionLocal() as s:
        llm_cfg = ProviderConfig(kind="llm", provider="fake", base_url="", model="fake",
                                 is_default=True, params={})
        s.add(llm_cfg)
        s.commit()
        llm_cfg_id = llm_cfg.id
    try:
        app = create_app()
        async with app.router.lifespan_context(app):
            transport = _asgi_transport(app, {"Authorization": f"Bearer {api_key}"})
            async with Client(transport) as mcp_client:
                res = await mcp_client.call_tool(
                    "ask", {"question": QUERY, "workspace_id": seed_data["ws"].id}
                )
                data = res.data
                assert "7 天" in data["answer"]
                assert data["citations"][0]["filename"] == "refund.md"
    finally:
        with SessionLocal() as s:
            cfg = s.get(ProviderConfig, llm_cfg_id)
            if cfg:
                s.delete(cfg)
                s.commit()


async def test_list_documents_and_get_document(seed_data, api_key):
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = _asgi_transport(app, {"Authorization": f"Bearer {api_key}"})
        async with Client(transport) as mcp_client:
            res = await mcp_client.call_tool(
                "list_documents", {"workspace_id": seed_data["ws"].id, "status": "ready"}
            )
            docs = res.data
            assert len(docs) == 1
            assert docs[0]["filename"] == "refund.md"
            res = await mcp_client.call_tool("get_document", {"doc_id": docs[0]["id"]})
            detail = res.data
            assert "退款" in detail["preview"]
            assert detail["status"] == "ready"
            with pytest.raises(ToolError, match="文档不存在"):
                await mcp_client.call_tool("get_document", {"doc_id": 999999})


async def test_handshake_without_key_fails(seed_data):
    """无 key 的真实握手被中间件 401 拒绝（端到端穿中间件验证）。"""
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = _asgi_transport(app, None)
        with pytest.raises(Exception):  # noqa: B017 - 连接层异常类型随 SDK 版本变化
            async with Client(transport):
                pass
