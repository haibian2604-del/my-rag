"""add documents.summary for auto document summarization

文档自动摘要（M5-T2）：documents 加可空 TEXT 列 summary。
摄取成功置 ready 后由 LLM 独立生成摘要写入；失败/无 LLM 时保持 NULL，
不影响摄取状态机。

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-09-07 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: str | Sequence[str] | None = 'e7f8a9b0c1d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('summary', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'summary')
