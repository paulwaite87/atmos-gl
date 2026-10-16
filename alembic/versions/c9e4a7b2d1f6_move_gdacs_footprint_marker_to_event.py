"""move the gdacs footprint fetched-at marker from the footprint rows to the event

An event whose GDACS footprint is only a (skipped) centroid has no footprint rows, so
a marker kept on them could never record "already fetched" and the collector would
re-download the empty footprint every cycle.

Revision ID: c9e4a7b2d1f6
Revises: b8d3f6a1c2e5
Create Date: 2026-10-16 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9e4a7b2d1f6'
down_revision: Union[str, Sequence[str], None] = 'b8d3f6a1c2e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('gdacs_events', sa.Column('footprint_modified', sa.String(length=32), nullable=True))
    op.drop_column('gdacs_footprints', 'source_modified')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('gdacs_footprints', sa.Column('source_modified', sa.String(length=32), nullable=True))
    op.drop_column('gdacs_events', 'footprint_modified')
