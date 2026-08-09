"""Add the Google Sheet-backed recruit directory.

Revision ID: 0011_recruit_directory
Revises: 0010_visible_managed_passwords
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_recruit_directory"
down_revision = "0010_visible_managed_passwords"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("recruit_directory"):
        op.create_table(
            "recruit_directory",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source_key", sa.String(length=64), nullable=False),
            sa.Column("source_row", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("phone_number", sa.String(length=100), nullable=True),
            sa.Column("date_of_birth", sa.Date(), nullable=True),
            sa.Column("date_of_birth_raw", sa.String(length=100), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_key"),
        )
    inspector = sa.inspect(bind)
    if not inspector.has_table("recruit_directory_state"):
        op.create_table(
            "recruit_directory_state",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("source_url", sa.String(length=500), nullable=False),
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.PrimaryKeyConstraint("id"),
        )
    recruit_columns = {column["name"] for column in sa.inspect(bind).get_columns("recruits")}
    if "directory_id" not in recruit_columns:
        with op.batch_alter_table("recruits") as batch:
            batch.add_column(sa.Column("directory_id", sa.String(length=36), nullable=True))
            batch.create_foreign_key(
                "fk_recruits_directory_id", "recruit_directory", ["directory_id"], ["id"]
            )


def downgrade() -> None:
    with op.batch_alter_table("recruits") as batch:
        batch.drop_constraint("fk_recruits_directory_id", type_="foreignkey")
        batch.drop_column("directory_id")
    op.drop_table("recruit_directory_state")
    op.drop_table("recruit_directory")
