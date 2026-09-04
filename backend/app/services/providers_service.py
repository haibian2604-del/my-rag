"""Provider 配置服务：CRUD、api_key 加解密、is_default 唯一性。"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret, encrypt_secret
from app.models.entities import ProviderConfig


def clear_other_defaults(s: Session, kind: str, exclude_id: int | None = None) -> None:
    """同 kind 只保留一个 default：清掉除 exclude_id 外的 is_default。"""
    others = s.execute(
        select(ProviderConfig).where(ProviderConfig.kind == kind)
    ).scalars().all()
    for p in others:
        if p.id != exclude_id and p.is_default:
            p.is_default = False


def mask_api_key(provider: ProviderConfig) -> str | None:
    return "***" if provider.api_key_encrypted else None


def provider_api_key(provider: ProviderConfig) -> str | None:
    if not provider.api_key_encrypted:
        return None
    try:
        return decrypt_secret(provider.api_key_encrypted)
    except Exception:  # noqa: BLE001 — 密钥轮换后旧密文解不开，按无 key 处理
        return None


def get_provider_or_404(s: Session, provider_id: int) -> ProviderConfig:
    p = s.get(ProviderConfig, provider_id)
    if not p:
        raise HTTPException(status_code=404, detail="provider 不存在")
    return p
