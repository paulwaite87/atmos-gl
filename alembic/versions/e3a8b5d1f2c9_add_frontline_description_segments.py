"""add frontline_snapshots.description_segments

Revision ID: e3a8b5d1f2c9
Revises: d2f7a9c4e1b8
Create Date: 2026-10-04 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e3a8b5d1f2c9'
down_revision: Union[str, Sequence[str], None] = 'd2f7a9c4e1b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'frontline_snapshots',
        sa.Column('description_segments', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('frontline_snapshots', 'description_segments')
