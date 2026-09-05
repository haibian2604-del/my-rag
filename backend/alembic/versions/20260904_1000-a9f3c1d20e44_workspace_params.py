"""workspace params

Revision ID: a9f3c1d20e44
Revises: 00228b7fafe2
Create Date: 2026-09-04 21:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9f3c1d20e44'
down_revision: str | Sequence[str] | None = '00228b7fafe2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'workspaces',
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('workspaces', 'params')
