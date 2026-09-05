import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.main import create_app
from app.models.entities import AppConfig, ProviderConfig


@pytest.fixture(autouse=True)
def _preserve_default_providers():
    """每条测试前临时摘除现有默认 provider 标记（不删行），测试后原样恢复。

    测试自建 fake 默认 provider 时，避免与共享库中用户真实默认配置并存
    （get_default_provider 按 id 升序会选中真实配置）；测试结束后保证
    用户真实配置的 is_default 状态与测试前一致。
    """
    with SessionLocal() as s:
        saved = [(r.id, r.kind) for r in s.execute(
            select(ProviderConfig).where(ProviderConfig.is_default.is_(True))
        ).scalars().all()]
        for r in s.execute(
            select(ProviderConfig).where(ProviderConfig.is_default.is_(True))
        ).scalars().all():
            r.is_default = False
        s.commit()
    yield
    with SessionLocal() as s:
        saved_ids = {pid for pid, _ in saved}
        # 同 kind 内清掉测试期间新设的默认，再恢复原有默认，保持唯一性
        for _pid, kind in saved:
            for r in s.execute(
                select(ProviderConfig).where(
                    ProviderConfig.kind == kind, ProviderConfig.is_default.is_(True))
            ).scalars().all():
                if r.id not in saved_ids:
                    r.is_default = False
        for pid, _kind in saved:
            r = s.get(ProviderConfig, pid)
            if r:
                r.is_default = True
        s.commit()


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
