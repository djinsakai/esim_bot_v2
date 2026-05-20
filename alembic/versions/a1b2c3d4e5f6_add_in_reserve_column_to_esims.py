"""add in_reserve column to esims

Revision ID: a1b2c3d4e5f6
Revises: 5cf4b971d4b5
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '5cf4b971d4b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('esims', sa.Column('in_reserve', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('esims', 'in_reserve')
