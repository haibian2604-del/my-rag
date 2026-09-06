import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.embedding_switch import router as embedding_switch_router
from app.api.settings import router as settings_router
from app.api.workspaces import router as workspaces_router
from app.core.config import settings

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.jobs.runner import recover_interrupted

        recover_interrupted()
    except Exception as e:  # noqa: BLE001 — 无库等场景不阻塞应用启动
        logger.warning("启动恢复中断文档失败: %s", e)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="my_rag", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router, prefix="/api")
    app.include_router(documents_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(embedding_switch_router, prefix="/api")
    app.include_router(workspaces_router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    # SPA 托管（容器内有前端构建产物时生效）
    from app.api.spa import mount_spa

    mount_spa(app, Path(__file__).parent / "static")

    return app


app = create_app()

if __name__ == "__main__":
    # 本地开发入口：uv run python -m app.main （默认端口 8001，避免与 oMLX 的 8000 冲突）
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
