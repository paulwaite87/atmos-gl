"""replace volcanoes table with volcanic_activity

Revision ID: 9f1a1798f7c4
Revises: 9c1a2f4e6b8d
Create Date: 2026-07-30 21:04:42.586319

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

# revision identifiers, used by Alembic.
revision: str = '9f1a1798f7c4'
down_revision: Union[str, Sequence[str], None] = '9c1a2f4e6b8d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index('idx_volcano_geom', table_name='volcanoes', postgresql_using='gist')
    op.drop_index('idx_volcano_filters', table_name='volcanoes')
    op.drop_table('volcanoes')

    op.create_table(
        'volcanic_activity',
        sa.Column('vnum', sa.String(length=20), nullable=False),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('country', sa.Text(), nullable=True),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column(
            'geom',
            Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.Column('activity_type', sa.Text(), nullable=True),
        sa.Column('report_description', sa.Text(), nullable=True),
        sa.Column('hans_color_code', sa.String(length=10), nullable=True),
        sa.Column('hans_alert_level', sa.String(length=20), nullable=True),
        sa.Column('hans_notice_url', sa.Text(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('vnum'),
    )
    op.create_index('idx_volcanic_activity_geom', 'volcanic_activity', ['geom'], unique=False, postgresql_using='gist')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_volcanic_activity_geom', table_name='volcanic_activity', postgresql_using='gist')
    op.drop_table('volcanic_activity')

    op.create_table(
        'volcanoes',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('lat', sa.Float(), nullable=True),
        sa.Column('lon', sa.Float(), nullable=True),
        sa.Column('vei', sa.Integer(), nullable=True),
        sa.Column('significant', sa.Boolean(), nullable=True),
        sa.Column('erupt_date_code', sa.String(length=10), nullable=True),
        sa.Column(
            'geom',
            Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_volcano_geom', 'volcanoes', ['geom'], unique=False, postgresql_using='gist')
    op.create_index('idx_volcano_filters', 'volcanoes', ['vei', 'significant', 'erupt_date_code'], unique=False)
