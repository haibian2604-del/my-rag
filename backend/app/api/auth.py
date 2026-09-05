from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.core.auth import COOKIE_NAME, create_token, get_auth_config, verify_password
from app.core.db import SessionLocal

# 认证路由本身不挂 require_auth（login 需免密可达）
router = APIRouter()


class LoginIn(BaseModel):
    password: str


@router.post("/auth/login", status_code=204)
def login(body: LoginIn, response: Response):
    with SessionLocal() as s:
        cfg = get_auth_config(s)
    if not verify_password(body.password, cfg["password_hash"]):
        raise HTTPException(status_code=401, detail="密码错误")
    response.set_cookie(
        COOKIE_NAME, create_token(), httponly=True, path="/", samesite="lax"
    )


@router.post("/auth/logout", status_code=204)
def logout():
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp
