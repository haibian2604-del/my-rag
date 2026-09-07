"""API Key 管理端点（M7-T1，设置页用，非 MCP）。

全部挂 require_auth（浏览器 Cookie）。明文 key 仅在创建响应返回一次，
列表与吊销响应均不携带明文或哈希。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, select

from app.core.auth import require_auth
from app.core.db import SessionLocal
from app.models.entities import ApiKey
from app.services import api_keys_service

router = APIRouter(dependencies=[Depends(require_auth)])


class KeyIn(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("密钥名称不能为空")
        if len(v) > 100:
            raise ValueError("密钥名称过长（≤100）")
        return v


class KeyCreatedOut(BaseModel):
    """创建响应：明文 key 仅此一次返回。"""
    id: int
    name: str
    key_prefix: str
    key: str


class KeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None


@router.post("/keys", response_model=KeyCreatedOut, status_code=201)
def create_key(body: KeyIn):
    row, raw = api_keys_service.generate(body.name)
    return KeyCreatedOut(
        id=row.id, name=row.name, key_prefix=row.key_prefix, key=raw
    )


@router.get("/keys", response_model=list[KeyOut])
def list_keys():
    with SessionLocal() as s:
        rows = s.execute(select(ApiKey).order_by(ApiKey.id)).scalars().all()
        return [
            KeyOut(
                id=r.id,
                name=r.name,
                key_prefix=r.key_prefix,
                created_at=r.created_at.isoformat(),
                last_used_at=r.last_used_at.isoformat() if r.last_used_at else None,
            )
            for r in rows
        ]


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(key_id: int):
    """吊销（删除）key：删除后 verify 立即失效。"""
    with SessionLocal() as s:
        if not s.get(ApiKey, key_id):
            raise HTTPException(status_code=404, detail="密钥不存在")
        s.execute(delete(ApiKey).where(ApiKey.id == key_id))
        s.commit()
