"""add aircraft tables

Revision ID: 21e14b6616d9
Revises: c5c53c2963c9
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

# revision identifiers, used by Alembic.
revision: str = '21e14b6616d9'
down_revision: Union[str, Sequence[str], None] = 'c5c53c2963c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'aircraft',
        sa.Column('hex', sa.String(length=12), nullable=False),
        sa.Column('registration', sa.String(length=20), nullable=True),
        sa.Column('aircraft_type', sa.String(length=10), nullable=True),
        sa.Column('category', sa.String(length=4), nullable=True),
        sa.Column('flight', sa.String(length=20), nullable=True),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column(
            'geom',
            Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.Column('alt_baro_ft', sa.REAL(), nullable=True),
        sa.Column('on_ground', sa.Boolean(), nullable=True),
        sa.Column('gs', sa.REAL(), nullable=True),
        sa.Column('track', sa.REAL(), nullable=True),
        sa.Column('baro_rate', sa.REAL(), nullable=True),
        sa.Column('nav_altitude_mcp', sa.REAL(), nullable=True),
        sa.Column('squawk', sa.String(length=4), nullable=True),
        sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('last_position_update', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('hex'),
    )
    op.create_index('idx_aircraft_geom', 'aircraft', ['geom'], unique=False, postgresql_using='gist')
    op.create_index('idx_aircraft_last_seen', 'aircraft', ['last_seen'], unique=False)

    op.create_table(
        'aircraft_track',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('hex', sa.String(length=12), nullable=False),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column(
            'geom',
            Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.Column('alt_baro_ft', sa.REAL(), nullable=True),
        sa.Column('on_ground', sa.Boolean(), nullable=True),
        sa.Column('gs', sa.REAL(), nullable=True),
        sa.Column('track', sa.REAL(), nullable=True),
        sa.Column('baro_rate', sa.REAL(), nullable=True),
        sa.Column('category', sa.String(length=4), nullable=True),
        sa.Column('squawk', sa.String(length=4), nullable=True),
        sa.Column('acquired_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['hex'], ['aircraft.hex'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_aircraft_track_geom', 'aircraft_track', ['geom'], unique=False, postgresql_using='gist')
    op.create_index('idx_aircraft_track_hex', 'aircraft_track', ['hex'], unique=False)
    op.create_index('idx_aircraft_track_time', 'aircraft_track', ['acquired_at'], unique=False)

    op.create_table(
        'aircraft_interest',
        sa.Column('viewer_id', sa.String(length=64), nullable=False),
        sa.Column('west', sa.Float(), nullable=False),
        sa.Column('south', sa.Float(), nullable=False),
        sa.Column('east', sa.Float(), nullable=False),
        sa.Column('north', sa.Float(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('viewer_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('aircraft_interest')

    op.drop_index('idx_aircraft_track_time', table_name='aircraft_track')
    op.drop_index('idx_aircraft_track_hex', table_name='aircraft_track')
    op.drop_index('idx_aircraft_track_geom', table_name='aircraft_track', postgresql_using='gist')
    op.drop_table('aircraft_track')

    op.drop_index('idx_aircraft_last_seen', table_name='aircraft')
    op.drop_index('idx_aircraft_geom', table_name='aircraft', postgresql_using='gist')
    op.drop_table('aircraft')
