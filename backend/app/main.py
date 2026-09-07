import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastmcp.utilities.lifespan import combine_lifespans

from app.api.agent import AGENT_CAPABILITY_KEY
from app.api.agent import router as agent_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.embedding_switch import router as embedding_switch_router
from app.api.keys import router as keys_router
from app.api.settings import router as settings_router
from app.api.workspaces import router as workspaces_router
from app.core.config import settings
from app.core.db import SessionLocal
from app.models.entities import AppConfig
from app.services.mcp_server import BearerAuthMiddleware, mcp_http_app

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


async def _probe_agent_capability() -> None:
    """后台探测默认 LLM 是否支持 tools：发一次最小 tools 请求（2s 超时，HTTP 200 即可用）。

    全程吞错：未配置 LLM 时安静跳过；任何失败写 false（探测失败=不可用）。
    绝不抛出、绝不阻塞启动。
    """
    try:
        from app.services.ingestion.pipeline import default_provider_or_none
        from app.services.providers_service import decrypt_api_key

        with SessionLocal() as s:
            cfg = default_provider_or_none(s, "llm")
            if cfg is None:
                return  # 未配置 LLM：安静跳过，不写结果
            headers = {}
            api_key = decrypt_api_key(cfg)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            url = cfg.base_url.rstrip("/") + "/chat/completions"
            payload = {
                "model": cfg.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                # 最小 tools 请求：探测端点是否支持 function calling 参数
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "noop",
                        "description": "探测用空工具",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }],
            }
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
            available = resp.status_code == 200
        with SessionLocal() as s:
            s.merge(AppConfig(key=AGENT_CAPABILITY_KEY, value={"available": available}))
            s.commit()
    except Exception as e:  # noqa: BLE001 — 探测失败=不可用，且绝不影响启动
        logger.warning("agent capability 探测失败: %s", e)
        try:
            with SessionLocal() as s:
                s.merge(AppConfig(key=AGENT_CAPABILITY_KEY, value={"available": False}))
                s.commit()
        except Exception as e2:  # noqa: BLE001 — 落库也失败时只记日志，绝不影响启动
            logger.warning("agent capability 探测结果落库失败: %s", e2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.jobs.runner import recover_interrupted

        recover_interrupted()
    except Exception as e:  # noqa: BLE001 — 无库等场景不阻塞应用启动
        logger.warning("启动恢复中断文档失败: %s", e)
    # capability 探测放后台 task：绝不阻塞启动、绝不拖慢测试
    task = asyncio.create_task(_probe_agent_capability())
    yield
    task.cancel()


def create_app() -> FastAPI:
    # lifespan 接线（Spec 指定）：fastmcp 的 combine_lifespans 把主应用
    # （recover_interrupted 等）与 MCP 会话管理器（StreamableHTTPSessionManager）
    # 合并为单一 ASGI lifespan，先后进入/退出，互不抢夺。
    combined_lifespan = combine_lifespans(lifespan, mcp_http_app.lifespan)
    app = FastAPI(title="my_rag", docs_url=None, redoc_url=None, lifespan=combined_lifespan)
    # Bearer 中间件仅对 /mcp 路径生效（内部判断），其余路由零行为变化
    app.add_middleware(BearerAuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router, prefix="/api")
    app.include_router(agent_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(embedding_switch_router, prefix="/api")
    app.include_router(workspaces_router, prefix="/api")
    app.include_router(keys_router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    # MCP 子应用（Streamable HTTP）：最终端点 URL = /mcp
    app.mount("/mcp", mcp_http_app)

    # SPA 托管（容器内有前端构建产物时生效）
    from app.api.spa import mount_spa

    mount_spa(app, Path(__file__).parent / "static")

    return app


app = create_app()

if __name__ == "__main__":
    # 本地开发入口：uv run python -m app.main （默认端口 8001，避免与 oMLX 的 8000 冲突）
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
