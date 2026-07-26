"""add progress columns to process_status

Revision ID: 8285d8a76145
Revises: bc3cbe8b0d9a
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8285d8a76145'
down_revision: Union[str, Sequence[str], None] = 'bc3cbe8b0d9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('process_status', sa.Column('progress_current', sa.Integer(), nullable=True))
    op.add_column('process_status', sa.Column('progress_total', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('process_status', 'progress_total')
    op.drop_column('process_status', 'progress_current')
