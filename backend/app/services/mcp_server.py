"""MCP 服务器（M7-T2）：把知识库以只读五工具暴露给外部 Agent。

架构（Spec docs/04 §三）：
- FastMCP("zhifu") 实例 + 五个只读工具（全部复用既有服务层，不复制业务逻辑）；
- mcp.http_app(path="/") 生成 Streamable HTTP 子应用，由 main.py 挂载到 /mcp；
- McpGateMiddleware：仅作用于 /mcp 路径的纯 ASGI 中间件，
  按 app_config 的 mcp_enabled 开关门禁（默认关；关闭返回 403）。
  开启后任何 MCP 客户端可直接 HTTP 连接（无认证），请在可信内网使用。
- lifespan 共处由 main.py 用 fastmcp 的 combine_lifespans 合并解决。
"""
import json
import logging

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from sqlalchemy import select as sa_select
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.db import SessionLocal
from app.models.entities import AppConfig, Chunk, Document, Workspace
from app.services.chat.llm_util import llm_complete
from app.services.chat.service import SYSTEM_PROMPT, LLMNotConfiguredError, get_llm_or_raise
from app.services.retrieval.context import build_context
from app.services.retrieval.search import retrieve

logger = logging.getLogger(__name__)

# ============ FastMCP 实例与五个只读工具 ============

mcp = FastMCP("zhifu")


def _resolve_workspace_id(s, workspace_id: int | None) -> int:
    """workspace_id 缺省时：单工作区自动选中，多工作区报可读错误列出可选 id。"""
    if workspace_id is not None:
        if not s.get(Workspace, workspace_id):
            raise ToolError(f"工作区不存在：workspace_id={workspace_id}")
        return workspace_id
    workspaces = s.execute(sa_select(Workspace).order_by(Workspace.id)).scalars().all()
    if not workspaces:
        raise ToolError("知识库中还没有任何工作区")
    if len(workspaces) == 1:
        return workspaces[0].id
    options = "；".join(f"{w.id}（{w.name}）" for w in workspaces)
    raise ToolError(f"存在多个工作区，请显式指定 workspace_id。可选：{options}")


@mcp.tool
async def list_workspaces() -> list[dict]:
    """列出知识库中所有工作区（id、名称、描述）。"""
    with SessionLocal() as s:
        rows = s.execute(sa_select(Workspace).order_by(Workspace.id)).scalars().all()
        return [{"id": w.id, "name": w.name, "description": w.description or ""} for w in rows]


@mcp.tool
async def search(query: str, workspace_id: int | None = None, top_k: int = 5) -> dict:
    """在知识库中做混合检索（向量 + 全文），返回最相关的 chunk 列表。

    workspace_id 缺省时单工作区自动选中；top_k 默认 5。
    """
    with SessionLocal() as s:
        ws_id = _resolve_workspace_id(s, workspace_id)
    hits = await retrieve(ws_id, query, top_k=top_k)
    return {
        "query": query,
        "workspace_id": ws_id,
        "hits": [
            {
                "filename": h["filename"],
                "heading_path": h.get("heading_path") or "",
                "page_no": h.get("page_no"),
                "content": h["content"],
                "score": round(h["score"], 6),
            }
            for h in hits
        ],
    }


@mcp.tool
async def ask(question: str, workspace_id: int | None = None) -> dict:
    """对知识库做一次完整 RAG 问答：检索 → 组装上下文 → LLM 生成回答与引用。

    LLM 未配置时返回明确的工具错误（不 500）；workspace_id 规则同 search。
    """
    with SessionLocal() as s:
        ws_id = _resolve_workspace_id(s, workspace_id)
        try:
            llm = get_llm_or_raise(s)
        except LLMNotConfiguredError:
            raise ToolError("未配置 LLM 模型，请先在设置页配置默认 LLM 后再使用 ask 工具")
        hits = await retrieve(ws_id, question)
        ctx, citations = build_context(hits)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + (f"\n\n资料：\n{ctx}" if ctx else "")},
            {"role": "user", "content": question},
        ]
        answer = await llm_complete(llm, messages)
    if answer is None:
        raise ToolError("LLM 调用失败或超时，请稍后重试")
    return {"question": question, "workspace_id": ws_id, "answer": answer, "citations": citations}


@mcp.tool
async def list_documents(workspace_id: int, status: str | None = None) -> list[dict]:
    """列出指定工作区下的文档（可按 status 过滤，如 ready/failed/pending）。"""
    with SessionLocal() as s:
        ws_id = _resolve_workspace_id(s, workspace_id)  # 校验工作区存在
        stmt = (
            sa_select(Document)
            .where(Document.workspace_id == ws_id)
            .order_by(Document.id)
        )
        if status:
            stmt = stmt.where(Document.status == status)
        rows = s.execute(stmt).scalars().all()
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "status": d.status,
                "summary": d.summary or "",
            }
            for d in rows
        ]


@mcp.tool
async def get_document(doc_id: int) -> dict:
    """获取文档详情：状态、摘要、正文预览（前 500 字）。不存在时返回工具错误。"""
    with SessionLocal() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            raise ToolError(f"文档不存在：doc_id={doc_id}")
        chunks = (
            s.execute(
                sa_select(Chunk)
                .where(Chunk.document_id == doc_id)
                .order_by(Chunk.ordinal)
            )
            .scalars()
            .all()
        )
        full_text = "\n".join(c.content for c in chunks)
        return {
            "id": doc.id,
            "workspace_id": doc.workspace_id,
            "filename": doc.filename,
            "status": doc.status,
            "summary": doc.summary or "",
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "preview": full_text[:500],
        }


# fastmcp 生成的 Starlette 子应用（Streamable HTTP），挂载点为 main.py 的 /mcp
mcp_http_app = mcp.http_app(path="/")


# ============ MCP 开关门禁中间件（仅作用于 /mcp） ============

_MCP_PATH = "/mcp"


def is_mcp_enabled() -> bool:
    """读 app_config 的 mcp_enabled 开关（默认关）。"""
    with SessionLocal() as s:
        row = s.get(AppConfig, "mcp_enabled")
        return bool(row.value.get("enabled", False)) if row else False


def set_mcp_enabled(enabled: bool) -> None:
    with SessionLocal() as s:
        row = s.get(AppConfig, "mcp_enabled")
        if row is None:
            s.add(AppConfig(key="mcp_enabled", value={"enabled": enabled}))
        else:
            row.value = {"enabled": enabled}
        s.commit()


class McpGateMiddleware:
    """纯 ASGI 中间件：仅对 /mcp 路径做开关门禁（无认证，由用户在设置页显式开启）。

    关闭 → 403 JSON（中文 detail）；开启 → 直接放行（HTTP 连接，无需密钥）。
    访问控制 = 开关本身：请在完全可信的内网环境开启。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path != _MCP_PATH and not path.startswith(_MCP_PATH + "/"):
            await self.app(scope, receive, send)
            return

        if not is_mcp_enabled():
            body = json.dumps({"detail": "MCP 服务未开启，请在设置页开启后连接"}).encode()
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        # 精确命中 /mcp（无尾斜杠）时改写为 /mcp/，避免 Mount 内部再发 307 重定向——
        # 部分严格的 MCP 客户端不跟随 POST 重定向
        if path == _MCP_PATH:
            scope = dict(scope)
            scope["path"] = _MCP_PATH + "/"

        await self.app(scope, receive, send)
