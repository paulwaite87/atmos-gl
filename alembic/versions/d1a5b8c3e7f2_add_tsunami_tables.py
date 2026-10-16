"""add tsunami_events, tsunami_event_zones and tsunami_zones tables

Revision ID: d1a5b8c3e7f2
Revises: c9e4a7b2d1f6
Create Date: 2026-10-16 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

# revision identifiers, used by Alembic.
revision: str = 'd1a5b8c3e7f2'
down_revision: Union[str, Sequence[str], None] = 'c9e4a7b2d1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'tsunami_events',
        sa.Column('centre', sa.String(length=4), nullable=False),
        sa.Column('event_id', sa.String(length=32), nullable=False),
        sa.Column('category', sa.String(length=12), nullable=True),
        sa.Column('bulletin_number', sa.Integer(), nullable=True),
        sa.Column('location_name', sa.Text(), nullable=True),
        sa.Column('magnitude', sa.Double(), nullable=True),
        sa.Column('magnitude_type', sa.String(length=8), nullable=True),
        sa.Column('depth_km', sa.Double(), nullable=True),
        sa.Column('origin_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires', sa.DateTime(timezone=True), nullable=True),
        sa.Column('bulletin_url', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column(
            'geom',
            Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('centre', 'event_id'),
    )
    op.create_index('idx_tsunami_events_geom', 'tsunami_events', ['geom'], postgresql_using='gist')
    op.create_index('idx_tsunami_events_expires', 'tsunami_events', ['expires'])

    op.create_table(
        'tsunami_event_zones',
        sa.Column('centre', sa.String(length=4), nullable=False),
        sa.Column('event_id', sa.String(length=32), nullable=False),
        sa.Column('zone_code', sa.String(length=8), nullable=False),
        sa.Column('category', sa.String(length=12), nullable=False),
        sa.ForeignKeyConstraint(
            ['centre', 'event_id'],
            ['tsunami_events.centre', 'tsunami_events.event_id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('centre', 'event_id', 'zone_code'),
    )

    op.create_table(
        'tsunami_zones',
        sa.Column('zone_code', sa.String(length=8), nullable=False),
        sa.Column(
            'geom',
            Geometry(geometry_type='GEOMETRY', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('zone_code'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('tsunami_zones')
    op.drop_table('tsunami_event_zones')
    op.drop_index('idx_tsunami_events_expires', table_name='tsunami_events')
    op.drop_index('idx_tsunami_events_geom', table_name='tsunami_events')
    op.drop_table('tsunami_events')
