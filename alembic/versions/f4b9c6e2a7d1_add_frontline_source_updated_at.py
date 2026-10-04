"""add frontline_snapshots.source_updated_at

Revision ID: f4b9c6e2a7d1
Revises: e3a8b5d1f2c9
Create Date: 2026-10-04 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f4b9c6e2a7d1'
down_revision: Union[str, Sequence[str], None] = 'e3a8b5d1f2c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'frontline_snapshots',
        sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('frontline_snapshots', 'source_updated_at')
