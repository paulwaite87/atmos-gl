"""add world_event_articles table

Revision ID: c8a4f1e2d9b3
Revises: b5d2e8f4a1c7
Create Date: 2026-10-02 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c8a4f1e2d9b3'
down_revision: Union[str, Sequence[str], None] = 'b5d2e8f4a1c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'world_event_articles',
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('headline', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column(
            'fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('url'),
    )
    # urls_needing_preview() joins world_events.source_url against this table.
    op.create_index('idx_world_events_source_url', 'world_events', ['source_url'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_world_events_source_url', table_name='world_events')
    op.drop_table('world_event_articles')
