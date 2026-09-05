from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.entities import AppConfig

COOKIE_NAME = "rag_token"
AUTH_KEY = "auth"
TOKEN_TTL_DAYS = 30


def get_auth_config(s) -> dict:
    """读 AppConfig 表 key="auth" 的认证配置，无记录时默认免密。"""
    row = s.get(AppConfig, AUTH_KEY)
    if row is None:
        return {"enabled": False, "password_hash": None}
    return {
        "enabled": bool(row.value.get("enabled", False)),
        "password_hash": row.value.get("password_hash"),
    }


def set_auth_config(s, enabled: bool, password: str | None = None) -> None:
    """写入认证配置；password 非空时设置 bcrypt 哈希（开关接口由 Task 9 暴露）。"""
    password_hash = (
        bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode() if password else None
    )
    value = {"enabled": enabled, "password_hash": password_hash}
    row = s.get(AppConfig, AUTH_KEY)
    if row is None:
        s.add(AppConfig(key=AUTH_KEY, value=value))
    else:
        row.value = value
    s.commit()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token() -> str:
    payload = {
        "sub": "owner",
        "exp": datetime.now(UTC) + timedelta(days=TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def require_auth(request: Request) -> None:
    """APIRouter 级依赖：enabled=false 放行（本机免密）；否则校验 JWT Cookie。"""
    with SessionLocal() as s:
        cfg = get_auth_config(s)
    if not cfg["enabled"]:
        return
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="登录已失效") from None
