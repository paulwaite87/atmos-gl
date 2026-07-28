"""add viewport_state table

Revision ID: 9c1a2f4e6b8d
Revises: f4a1c9d7e2b3
Create Date: 2026-07-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9c1a2f4e6b8d'
down_revision: Union[str, Sequence[str], None] = 'f4a1c9d7e2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'viewport_state',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('lat', sa.REAL(), nullable=False),
        sa.Column('lon', sa.REAL(), nullable=False),
        sa.Column('zoom', sa.REAL(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('viewport_state')
