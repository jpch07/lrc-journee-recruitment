"""Add global owner accounts and workspace ownership.

Revision ID: 0015_global_owner_accounts
Revises: 0014_recruitment_tenancy
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "0015_global_owner_accounts"
down_revision = "0014_recruitment_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("platform_accounts"):
        op.create_table(
            "platform_accounts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("username", sa.String(200), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(500), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not inspector.has_table("platform_sessions"):
        op.create_table(
            "platform_sessions",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("account_id", sa.String(36), sa.ForeignKey("platform_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("csrf_token", sa.String(80), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    system_columns = {item["name"] for item in sa.inspect(bind).get_columns("assessment_systems")}
    if "owner_platform_account_id" not in system_columns:
        op.add_column("assessment_systems", sa.Column("owner_platform_account_id", sa.String(36), nullable=True))
    account_columns = {item["name"] for item in sa.inspect(bind).get_columns("user_accounts")}
    if "platform_account_id" not in account_columns:
        op.add_column("user_accounts", sa.Column("platform_account_id", sa.String(36), nullable=True))
    if bind.dialect.name != "sqlite":
        system_fks = {item.get("name") for item in sa.inspect(bind).get_foreign_keys("assessment_systems")}
        account_fks = {item.get("name") for item in sa.inspect(bind).get_foreign_keys("user_accounts")}
        if "fk_assessment_systems_owner_platform_account_id" not in system_fks:
            op.create_foreign_key(
                "fk_assessment_systems_owner_platform_account_id",
                "assessment_systems", "platform_accounts",
                ["owner_platform_account_id"], ["id"], ondelete="SET NULL",
            )
        if "fk_user_accounts_platform_account_id" not in account_fks:
            op.create_foreign_key(
                "fk_user_accounts_platform_account_id",
                "user_accounts", "platform_accounts",
                ["platform_account_id"], ["id"], ondelete="SET NULL",
            )

    now = datetime.now(timezone.utc)
    owners = list(bind.execute(sa.text(
        "select ua.id, ua.system_id, ua.username, ua.password_hash "
        "from user_accounts ua where ua.is_owner = :owner order by ua.created_at"
    ), {"owner": True}))
    platform_by_name: dict[str, str] = {}
    for user_id, system_id, username, password_hash in owners:
        key = username.casefold()
        platform_id = platform_by_name.get(key)
        if not platform_id:
            existing = bind.execute(
                sa.text("select id from platform_accounts where lower(username)=lower(:username)"),
                {"username": username},
            ).scalar()
            platform_id = existing or str(uuid.uuid4())
            if not existing:
                bind.execute(sa.text(
                    "insert into platform_accounts (id, username, password_hash, active, version, created_at, updated_at) "
                    "values (:id, :username, :password_hash, :active, 1, :created_at, :updated_at)"
                ), {
                    "id": platform_id, "username": username, "password_hash": password_hash,
                    "active": True, "created_at": now, "updated_at": now,
                })
            platform_by_name[key] = platform_id
        bind.execute(sa.text("update user_accounts set platform_account_id=:platform_id where id=:id"),
                     {"platform_id": platform_id, "id": user_id})
        bind.execute(sa.text("update assessment_systems set owner_platform_account_id=:platform_id where id=:id"),
                     {"platform_id": platform_id, "id": system_id})


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_user_accounts_platform_account_id", "user_accounts", type_="foreignkey")
        op.drop_constraint("fk_assessment_systems_owner_platform_account_id", "assessment_systems", type_="foreignkey")
    op.drop_column("user_accounts", "platform_account_id")
    op.drop_column("assessment_systems", "owner_platform_account_id")
    op.drop_table("platform_sessions")
    op.drop_table("platform_accounts")
