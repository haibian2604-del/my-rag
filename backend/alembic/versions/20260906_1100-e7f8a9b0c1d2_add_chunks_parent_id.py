"""add chunks.parent_id for parent-child chunking

父子分块（A2）：chunks 加自引用列 parent_id（可空，索引，ondelete CASCADE）。
语义：父块 parent_id 为空仅存全文；子块 parent_id 指向父块，是唯一被
嵌入/FTS/检索单元。检索叶子 = parent_id 非空 OR 无子块指向自己——
旧文档未回填（全为 parent_id 空且无子块）时父块自身即叶子，行为不变。

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-09-06 11:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: str | Sequence[str] | None = 'd5e6f7a8b9c0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'chunks',
        sa.Column('parent_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_chunks_parent_id_chunks', 'chunks', 'chunks',
        ['parent_id'], ['id'], ondelete='CASCADE',
    )
    op.create_index(op.f('ix_chunks_parent_id'), 'chunks', ['parent_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_chunks_parent_id'), table_name='chunks')
    op.drop_constraint('fk_chunks_parent_id_chunks', 'chunks', type_='foreignkey')
    op.drop_column('chunks', 'parent_id')
