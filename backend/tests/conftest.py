import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.main import create_app
from app.models.entities import AppConfig


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """每条测试前重置认证配置。

    测试直连共享开发库，若上一轮进程中断留下 auth 残留状态，
    依赖"默认免密"的测试会连锁失败；这里前置清空兜底。
    （不动 provider_configs，避免清掉真实的模型配置。）
    """
    with SessionLocal() as s:
        s.execute(delete(AppConfig).where(AppConfig.key == "auth"))
        s.commit()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
