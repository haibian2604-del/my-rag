from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import HTTPException, Request

from app.core.config import settings

COOKIE_NAME = "rag_token"
TOKEN_TTL_DAYS = 30


def hash_password(password: str) -> str:
    """bcrypt 哈希（gensalt）。"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": datetime.now(UTC) + timedelta(days=TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def require_auth(request: Request) -> None:
    """APIRouter 级依赖：校验 JWT Cookie，无 Cookie/无效/过期 → 401。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="登录已失效") from None
