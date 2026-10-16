"""add gdacs_events and gdacs_footprints tables

Revision ID: b8d3f6a1c2e5
Revises: a7c3e9f1b2d4
Create Date: 2026-10-16 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

# revision identifiers, used by Alembic.
revision: str = 'b8d3f6a1c2e5'
down_revision: Union[str, Sequence[str], None] = 'a7c3e9f1b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'gdacs_events',
        sa.Column('event_type', sa.String(length=2), nullable=False),
        sa.Column('event_id', sa.BigInteger(), nullable=False),
        sa.Column('episode_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('alert_level', sa.String(length=10), nullable=True),
        sa.Column('alert_score', sa.REAL(), nullable=True),
        sa.Column('severity', sa.Double(), nullable=True),
        sa.Column('severity_text', sa.Text(), nullable=True),
        sa.Column('severity_unit', sa.String(length=20), nullable=True),
        sa.Column('country', sa.Text(), nullable=True),
        sa.Column('iso3', sa.String(length=3), nullable=True),
        sa.Column('from_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('to_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('date_modified', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_current', sa.Boolean(), nullable=True),
        sa.Column('report_url', sa.Text(), nullable=True),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column(
            'geom',
            Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('event_type', 'event_id'),
    )
    op.create_index('idx_gdacs_events_geom', 'gdacs_events', ['geom'], postgresql_using='gist')
    op.create_index('idx_gdacs_events_to_date', 'gdacs_events', ['to_date'])

    op.create_table(
        'gdacs_footprints',
        sa.Column('event_type', sa.String(length=2), nullable=False),
        sa.Column('event_id', sa.BigInteger(), nullable=False),
        sa.Column('part_index', sa.Integer(), nullable=False),
        sa.Column('feature_class', sa.String(length=64), nullable=False),
        sa.Column('source_modified', sa.String(length=32), nullable=True),
        sa.Column(
            'geom',
            Geometry(geometry_type='GEOMETRY', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ['event_type', 'event_id'],
            ['gdacs_events.event_type', 'gdacs_events.event_id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('event_type', 'event_id', 'part_index'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('gdacs_footprints')
    op.drop_index('idx_gdacs_events_to_date', table_name='gdacs_events')
    op.drop_index('idx_gdacs_events_geom', table_name='gdacs_events')
    op.drop_table('gdacs_events')
