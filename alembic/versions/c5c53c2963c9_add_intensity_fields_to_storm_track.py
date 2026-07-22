"""add intensity fields to storm_track

Revision ID: c5c53c2963c9
Revises: d2a149aa87e0
Create Date: 2026-07-22 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c5c53c2963c9'
down_revision: Union[str, Sequence[str], None] = 'd2a149aa87e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('storm_track', sa.Column('wind_kt', sa.Integer(), nullable=True))
    op.add_column('storm_track', sa.Column('pressure_hpa', sa.Integer(), nullable=True))
    op.add_column('storm_track', sa.Column('category', sa.String(length=4), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('storm_track', 'category')
    op.drop_column('storm_track', 'pressure_hpa')
    op.drop_column('storm_track', 'wind_kt')
