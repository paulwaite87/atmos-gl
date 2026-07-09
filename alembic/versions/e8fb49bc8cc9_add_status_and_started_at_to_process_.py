"""add status and started_at to process_status

Revision ID: e8fb49bc8cc9
Revises: 41ed038c7bfd
Create Date: 2026-07-10 11:17:32.451973

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e8fb49bc8cc9'
down_revision: Union[str, Sequence[str], None] = '41ed038c7bfd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    NOTE: autogenerate also detected a large batch of DROP TABLE/INDEX statements for
    the postgis_tiger_geocoder extension's tables (tiger.*, topology.*, etc.) -- those
    aren't modeled in db/models.py (they belong to the postgis extension, not our
    schema), so autogenerate sees them as "not in metadata" and wants to drop them.
    Stripped out manually; this migration only touches process_status.
    """
    op.add_column('process_status', sa.Column('status', sa.Text(), server_default='idle', nullable=False))
    op.add_column('process_status', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('process_status', 'started_at')
    op.drop_column('process_status', 'status')
