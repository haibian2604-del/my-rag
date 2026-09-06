"""账号制认证测试：注册（仅 users 空时）/ 登录 / 登出 / 改密 / 未登录 401。

注意：conftest 的 _reset_auth_state 每条测试前清空 users 表，
因此这里直接用「裸」TestClient（不经过 conftest 的已登录 client）。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def raw_client() -> TestClient:
    return TestClient(create_app())


def test_status_unregistered(raw_client):
    resp = raw_client.get("/api/auth/status")
    assert resp.status_code == 200
    assert resp.json() == {"registered": False}


def test_register_first_user_sets_cookie(raw_client):
    resp = raw_client.post(
        "/api/auth/register", json={"username": "admin", "password": "secret-pw"}
    )
    assert resp.status_code == 204
    assert resp.cookies.get("rag_token")
    # 注册后状态变为已注册，且 Cookie 可访问业务 API
    assert raw_client.get("/api/auth/status").json() == {"registered": True}
    assert raw_client.get("/api/settings/providers").status_code == 200


def test_register_rejected_when_users_exist(raw_client):
    raw_client.post(
        "/api/auth/register", json={"username": "admin", "password": "secret-pw"}
    )
    resp = raw_client.post(
        "/api/auth/register", json={"username": "other", "password": "secret-pw"}
    )
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "bad name!", "password": "secret-pw"},  # 非法字符
        {"username": "", "password": "secret-pw"},  # 空
        {"username": "a" * 51, "password": "secret-pw"},  # 超长
        {"username": "admin", "password": "12345"},  # 密码过短
    ],
)
def test_register_invalid_input(raw_client, payload):
    resp = raw_client.post("/api/auth/register", json=payload)
    assert resp.status_code in (400, 422)


def test_login_success_and_fail(raw_client):
    raw_client.post(
        "/api/auth/register", json={"username": "admin", "password": "secret-pw"}
    )
    raw_client.post("/api/auth/logout")
    # 用户名或密码错误统一 401 提示，防枚举
    wrong_pw = raw_client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong-pw"}
    )
    wrong_user = raw_client.post(
        "/api/auth/login", json={"username": "nobody", "password": "secret-pw"}
    )
    for resp in (wrong_pw, wrong_user):
        assert resp.status_code == 401
        assert resp.json() == {"detail": "用户名或密码错误"}
    resp = raw_client.post(
        "/api/auth/login", json={"username": "admin", "password": "secret-pw"}
    )
    assert resp.status_code == 204
    assert resp.cookies.get("rag_token")
    assert raw_client.get("/api/settings/providers").status_code == 200


def test_logout_clears_session(raw_client):
    raw_client.post(
        "/api/auth/register", json={"username": "admin", "password": "secret-pw"}
    )
    assert raw_client.get("/api/settings/providers").status_code == 200
    assert raw_client.post("/api/auth/logout").status_code == 204
    assert raw_client.get("/api/settings/providers").status_code == 401


def test_change_password(raw_client):
    raw_client.post(
        "/api/auth/register", json={"username": "admin", "password": "old-pw-1"}
    )
    # 错误旧密码
    resp = raw_client.put(
        "/api/auth/password",
        json={"old_password": "wrong-pw", "new_password": "new-pw-1"},
    )
    assert resp.status_code == 401
    # 新密码过短
    resp = raw_client.put(
        "/api/auth/password",
        json={"old_password": "old-pw-1", "new_password": "12345"},
    )
    assert resp.status_code in (400, 422)
    # 正确改密
    resp = raw_client.put(
        "/api/auth/password",
        json={"old_password": "old-pw-1", "new_password": "new-pw-1"},
    )
    assert resp.status_code == 204
    raw_client.post("/api/auth/logout")
    assert (
        raw_client.post(
            "/api/auth/login", json={"username": "admin", "password": "old-pw-1"}
        ).status_code
        == 401
    )
    assert (
        raw_client.post(
            "/api/auth/login", json={"username": "admin", "password": "new-pw-1"}
        ).status_code
        == 204
    )


def test_business_api_401_when_no_users(raw_client):
    # 未初始化（users 为空）：业务 API 401，health 豁免
    assert raw_client.get("/api/health").status_code == 200
    assert raw_client.get("/api/workspaces").status_code == 401
    assert raw_client.get("/api/settings/providers").status_code == 401
    assert raw_client.get("/api/documents/1").status_code == 401
