"""add chunks fts

Revision ID: 093173b0dd72
Revises: a9f3c1d20e44
Create Date: 2026-09-05 18:23:04.177844

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '093173b0dd72'
down_revision: str | Sequence[str] | None = 'a9f3c1d20e44'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增 chunks.fts tsvector 列 + GIN 索引（不做数据回填，见 backfill_fts）。"""
    op.add_column('chunks', sa.Column('fts', postgresql.TSVECTOR(), nullable=True))
    op.create_index('idx_chunks_fts', 'chunks', ['fts'], postgresql_using='gin')


def downgrade() -> None:
    op.drop_index('idx_chunks_fts', table_name='chunks')
    op.drop_column('chunks', 'fts')
