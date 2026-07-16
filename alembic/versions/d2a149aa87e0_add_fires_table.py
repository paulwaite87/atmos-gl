"""add fires table

Revision ID: d2a149aa87e0
Revises: e8fb49bc8cc9
Create Date: 2026-07-16 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

# revision identifiers, used by Alembic.
revision: str = 'd2a149aa87e0'
down_revision: Union[str, Sequence[str], None] = 'e8fb49bc8cc9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'fires',
        sa.Column('id', sa.String(length=80), nullable=False),
        sa.Column('lat', sa.REAL(), nullable=True),
        sa.Column('lon', sa.REAL(), nullable=True),
        sa.Column('brightness', sa.REAL(), nullable=True),
        sa.Column('frp', sa.REAL(), nullable=True),
        sa.Column('confidence', sa.String(length=10), nullable=True),
        sa.Column('satellite', sa.String(length=20), nullable=True),
        sa.Column('daynight', sa.String(length=1), nullable=True),
        sa.Column('acq_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'geom',
            Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_fires_acq_time', 'fires', ['acq_time'], unique=False)
    op.create_index('idx_fires_geom', 'fires', ['geom'], unique=False, postgresql_using='gist')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_fires_geom', table_name='fires', postgresql_using='gist')
    op.drop_index('idx_fires_acq_time', table_name='fires')
    op.drop_table('fires')
