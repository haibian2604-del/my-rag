"""add per-dim HNSW expression partial indexes on chunk_embeddings

chunk_embeddings.embedding 列无 typmod（多维度并存），pgvector 不能直接建
HNSW，需对每个 dim 建表达式部分索引：((embedding::vector(N)) vector_cosine_ops)
WHERE dim = N。查询侧必须使用完全相同的 cast 表达式才能命中索引。

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-06 10:00:00.000000

"""
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: str | Sequence[str] | None = 'c4d5e6f7a8b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_dims() -> list[int]:
    rows = op.get_bind().execute(
        text("SELECT DISTINCT dim FROM chunk_embeddings")
    ).scalars().all()
    return sorted(int(d) for d in rows)


def upgrade() -> None:
    # DDL 单一出处：与 app/core/db.py ensure_vector_index 逐字共用，
    # 查询侧 cast 命中依赖表达式一致
    from app.core.db import ensure_vector_index

    for dim in _existing_dims():
        ensure_vector_index(dim)


def downgrade() -> None:
    for dim in _existing_dims():
        op.get_bind().execute(text(f"DROP INDEX IF EXISTS ix_ce_hnsw_{dim}"))
