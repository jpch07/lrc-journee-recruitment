"""Add owner-visible managed account passwords.

Revision ID: 0010_visible_managed_passwords
Revises: 0009_accounts_admin_evaluations
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0010_visible_managed_passwords"
down_revision = "0009_accounts_admin_evaluations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("user_accounts")}
    if "managed_password" not in columns:
        op.add_column("user_accounts", sa.Column("managed_password", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("user_accounts", "managed_password")
