import re

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.auth import COOKIE_NAME, create_token, current_username, hash_password, verify_password
from app.core.db import SessionLocal
from app.models.entities import User

# 认证路由本身不挂全局 require_auth（status/register/login 需免认证可达）；
# 需认证的接口（改密）单独声明依赖。
router = APIRouter()

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


class CredentialsIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


def _validate_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(
            status_code=400,
            detail="用户名仅限 1-50 位字母、数字、下划线或中划线",
        )
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")


def _user_count() -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(User)) or 0


def _set_session_cookie(response: Response, username: str) -> None:
    response.set_cookie(
        COOKIE_NAME, create_token(username), httponly=True, path="/", samesite="lax"
    )


def _authenticate(username: str, password: str) -> User | None:
    with SessionLocal() as s:
        user = s.scalar(select(User).where(User.username == username))
        if user is None or not verify_password(password, user.password_hash):
            return None
        s.expunge(user)
        return user


@router.get("/auth/status")
def status() -> dict:
    return {"registered": _user_count() > 0}


@router.post("/auth/register", status_code=204)
def register(body: CredentialsIn, response: Response):
    if _user_count() > 0:
        raise HTTPException(status_code=403, detail="已存在管理员账号，禁止注册")
    _validate_credentials(body.username, body.password)
    with SessionLocal() as s:
        s.add(User(username=body.username, password_hash=hash_password(body.password)))
        s.commit()
    _set_session_cookie(response, body.username)


@router.post("/auth/login", status_code=204)
def login(body: CredentialsIn, response: Response):
    # 不区分用户不存在/密码错误，统一提示（防用户名枚举）
    user = _authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _set_session_cookie(response, user.username)


@router.get("/auth/me")
def me(username: str = Depends(current_username)) -> dict:
    return {"username": username}


@router.post("/auth/logout", status_code=204)
def logout():
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.put("/auth/password", status_code=204)
def change_password(body: ChangePasswordIn,
                    username: str = Depends(current_username)):
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    with SessionLocal() as s:
        user = s.scalar(select(User).where(User.username == username))
        if user is None:
            raise HTTPException(status_code=401, detail="登录已失效")
        if not verify_password(body.old_password, user.password_hash):
            raise HTTPException(status_code=401, detail="旧密码错误")
        user.password_hash = hash_password(body.new_password)
        s.commit()
