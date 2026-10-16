"""add internet_outage_events table

Revision ID: e2b6c9d4f8a3
Revises: d1a5b8c3e7f2
Create Date: 2026-10-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e2b6c9d4f8a3'
down_revision: Union[str, Sequence[str], None] = 'd1a5b8c3e7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'internet_outage_events',
        sa.Column('country_code', sa.String(length=2), nullable=False),
        sa.Column('signal', sa.String(length=24), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('country_name', sa.Text(), nullable=True),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ongoing', sa.Boolean(), nullable=True),
        sa.Column('score', sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint('country_code', 'signal', 'start_time'),
    )
    op.create_index('idx_internet_outage_events_end_time', 'internet_outage_events', ['end_time'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_internet_outage_events_end_time', table_name='internet_outage_events')
    op.drop_table('internet_outage_events')
