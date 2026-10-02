"""add world_event_export_files table

Revision ID: b5d2e8f4a1c7
Revises: e7c23987ad6b
Create Date: 2026-10-02 19:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b5d2e8f4a1c7'
down_revision: Union[str, Sequence[str], None] = 'e7c23987ad6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'world_event_export_files',
        sa.Column('slot', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column(
            'processed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('slot'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('world_event_export_files')
