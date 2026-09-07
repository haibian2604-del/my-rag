"""API Key 服务（M7-T1）：生成 / 校验 / 记录最近使用。

安全约定：key 明文绝不入库（仅 bcrypt 哈希）、绝不写日志，
只在 generate 的返回值中出现一次（由创建响应返回给用户）。
"""
import secrets

import bcrypt
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.auth import hash_password
from app.core.db import SessionLocal
from app.models.entities import ApiKey

KEY_PREFIX_TAG = "zk-"  # 明文 key 固定前缀


def _new_raw_key() -> str:
    return KEY_PREFIX_TAG + secrets.token_urlsafe(24)


def generate(name: str) -> tuple[ApiKey, str]:
    """生成新 key：bcrypt 哈希入库，返回 (行, 明文)。明文仅此一次。"""
    raw = _new_raw_key()
    with SessionLocal() as s:
        row = ApiKey(
            name=name,
            key_prefix=raw[:8],  # 如 zk-1a2b，用于列表识别
            key_hash=hash_password(raw),
        )
        s.add(row)
        s.commit()
        return row, raw


def verify(raw_key: str) -> ApiKey | None:
    """校验明文 key：zk- 前缀 + 与库中所有哈希 bcrypt 比对。

    个人规模 key 数量极小（个位数），逐行比对可接受；
    bcrypt 自带盐，无法按哈希定位，只能全量比对。
    """
    if not raw_key.startswith(KEY_PREFIX_TAG):
        return None
    with SessionLocal() as s:
        for row in s.execute(select(ApiKey)).scalars().all():
            if bcrypt.checkpw(raw_key.encode(), row.key_hash.encode()):
                return row
    return None


def touch(key_id: int) -> None:
    """更新 last_used_at（独立 session，失败不影响调用方）。"""
    try:
        with SessionLocal() as s:
            row = s.get(ApiKey, key_id)
            if row:
                row.last_used_at = datetime.now(UTC)
                s.commit()
    except Exception:  # pragma: no cover - 记录使用时间失败不应阻断主链路
        pass
