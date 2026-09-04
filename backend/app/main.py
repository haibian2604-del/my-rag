from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.documents import router as documents_router
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.jobs.runner import recover_interrupted

        recover_interrupted()
    except Exception:  # noqa: BLE001, S110 — 无库等场景不阻塞应用启动
        pass
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
    app.include_router(documents_router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
