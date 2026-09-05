"""SPA 静态托管：托管前端构建产物，非 /api 路由回退到 index.html。

开发模式下 app/static 不存在时跳过挂载（由 Vite dev server 提供前端）。
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    if not index.exists():
        return

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    static_root = static_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # 未知 API 路径保持 404 JSON，不回退到页面
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        candidate = (static_dir / full_path).resolve()
        if candidate.is_file() and static_root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
