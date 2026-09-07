"""MCP 开关管理路由（浏览器登录态操作）。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import require_auth
from app.services.mcp_server import is_mcp_enabled, set_mcp_enabled

router = APIRouter(dependencies=[Depends(require_auth)])


class McpSettingsIn(BaseModel):
    enabled: bool


@router.get("/mcp/settings")
def get_mcp_settings() -> dict:
    return {"enabled": is_mcp_enabled()}


@router.put("/mcp/settings")
def update_mcp_settings(body: McpSettingsIn) -> dict:
    set_mcp_enabled(body.enabled)
    return {"enabled": body.enabled}
