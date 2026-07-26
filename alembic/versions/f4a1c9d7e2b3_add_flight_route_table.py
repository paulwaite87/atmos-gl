"""add flight_route table

Revision ID: f4a1c9d7e2b3
Revises: 8285d8a76145
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'f4a1c9d7e2b3'
down_revision: Union[str, Sequence[str], None] = '8285d8a76145'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'flight_route',
        sa.Column('flight', sa.String(length=20), nullable=False),
        sa.Column('stops', JSONB(), nullable=True),
        sa.Column('plausible', sa.Boolean(), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('flight'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('flight_route')
