"""Agent capability 接口：返回默认 LLM 是否支持 tools（启动探测结果）。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.models.entities import AppConfig

router = APIRouter(dependencies=[Depends(require_auth)])

# app_config 中的探测结果键（value 形如 {"available": true/false}）
AGENT_CAPABILITY_KEY = "agent_capability"


class CapabilityOut(BaseModel):
    available: bool


@router.get("/agent/capability", response_model=CapabilityOut)
def agent_capability() -> CapabilityOut:
    """读取启动探测写入的结果；未探测/探测失败均视为不可用。"""
    with SessionLocal() as s:
        row = s.get(AppConfig, AGENT_CAPABILITY_KEY)
        return CapabilityOut(available=bool(row and row.value.get("available")))
