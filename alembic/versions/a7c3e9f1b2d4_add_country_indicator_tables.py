"""add country_indicators and country_indicator_values tables

Revision ID: a7c3e9f1b2d4
Revises: f4b9c6e2a7d1
Create Date: 2026-10-05 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b2d4'
down_revision: Union[str, Sequence[str], None] = 'f4b9c6e2a7d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'country_indicators',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('unit', sa.Text(), nullable=True),
        sa.Column('short_unit', sa.Text(), nullable=True),
        sa.Column('citation', sa.Text(), nullable=True),
        sa.Column('source_last_updated', sa.String(length=32), nullable=True),
        sa.Column('next_update', sa.String(length=32), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'country_indicator_values',
        sa.Column('indicator_id', sa.String(length=64), nullable=False),
        sa.Column('country_code', sa.String(length=3), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('value', sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(['indicator_id'], ['country_indicators.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('indicator_id', 'country_code'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('country_indicator_values')
    op.drop_table('country_indicators')
