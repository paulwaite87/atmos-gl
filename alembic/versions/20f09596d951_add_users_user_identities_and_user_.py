"""add users, user_identities, and user_sessions tables

Revision ID: 20f09596d951
Revises: 9f1a1798f7c4
Create Date: 2026-08-09 18:50:17.683641

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20f09596d951'
down_revision: Union[str, Sequence[str], None] = '9f1a1798f7c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('is_admin', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_table(
        'user_identities',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('provider_user_id', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'provider_user_id', name='uq_user_identities_provider_subject'),
    )
    op.create_index('idx_user_identities_user_id', 'user_identities', ['user_id'])
    op.create_table(
        'user_sessions',
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token'),
    )
    op.create_index('idx_user_sessions_user_id', 'user_sessions', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_user_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_index('idx_user_identities_user_id', table_name='user_identities')
    op.drop_table('user_identities')
    op.drop_table('users')
