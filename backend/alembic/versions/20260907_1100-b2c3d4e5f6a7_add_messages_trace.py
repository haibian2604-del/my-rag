"""add messages.trace for agent tool timeline

Agent 问答模式（M6-T2）：messages 加可空 JSONB 列 trace，
agent 模式回答落库时写入工具调用时间线（[{tool, args, preview}]）；
RAG 模式保持 NULL，不影响既有行为。

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-07 11:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: str | Sequence[str] | None = 'f1a2b3c4d5e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('trace', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'trace')
