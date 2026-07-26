"""add health columns to process_status

Revision ID: bc3cbe8b0d9a
Revises: 21e14b6616d9
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'bc3cbe8b0d9a'
down_revision: Union[str, Sequence[str], None] = '21e14b6616d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('process_status', sa.Column('health', sa.String(length=20), nullable=True))
    op.add_column('process_status', sa.Column('health_detail', sa.Text(), nullable=True))
    op.add_column('process_status', sa.Column('health_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('process_status', 'health_at')
    op.drop_column('process_status', 'health_detail')
    op.drop_column('process_status', 'health')
