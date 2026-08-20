"""Scope accounts and operational data to one recruitment system.

Revision ID: 0014_recruitment_tenancy
Revises: 0013_configurable_assessment
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op


revision = "0014_recruitment_tenancy"
down_revision = "0013_configurable_assessment"
branch_labels = None
depends_on = None


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", (value or "assessment").casefold()).strip("-")
    return (clean or "assessment")[:90]


def _drop_single_unique(bind, table_name: str, column_name: str) -> None:
    names = [
        item.get("name") for item in sa.inspect(bind).get_unique_constraints(table_name)
        if item.get("column_names") == [column_name] and item.get("name")
    ]
    if names:
        with op.batch_alter_table(table_name) as batch:
            for name in names:
                batch.drop_constraint(name, type_="unique")


def upgrade() -> None:
    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        # Batch table replacement must temporarily bypass references from the
        # existing operational tables. Data is backfilled before constraints are
        # recreated, and enforcement is restored before the migration returns.
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    system_columns = {item["name"] for item in sa.inspect(bind).get_columns("assessment_systems")}
    if "slug" not in system_columns:
        with op.batch_alter_table("assessment_systems") as batch:
            batch.add_column(sa.Column("slug", sa.String(100), nullable=True))
        rows = list(bind.execute(sa.text("select id, name from assessment_systems order by created_at")))
        used: set[str] = set()
        for system_id, name in rows:
            base = _slug(name)
            candidate = base
            number = 2
            while candidate in used:
                candidate = f"{base[:86]}-{number}"
                number += 1
            used.add(candidate)
            bind.execute(sa.text("update assessment_systems set slug=:slug where id=:id"), {"slug": candidate, "id": system_id})
        with op.batch_alter_table("assessment_systems") as batch:
            batch.alter_column("slug", existing_type=sa.String(100), nullable=False)
            batch.create_unique_constraint("uq_assessment_system_slug", ["slug"])

    first_system_id = bind.execute(sa.text("select id from assessment_systems order by created_at limit 1")).scalar()
    roots = ("journeys", "recruit_directory", "recruit_directory_state", "evaluator_directory", "user_accounts", "audit_events")
    for table_name in roots:
        columns = {item["name"] for item in sa.inspect(bind).get_columns(table_name)}
        if "system_id" in columns:
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(sa.Column("system_id", sa.String(36), nullable=True))
        if first_system_id:
            bind.execute(sa.text(f"update {table_name} set system_id=:system_id where system_id is null"), {"system_id": first_system_id})
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("system_id", existing_type=sa.String(36), nullable=False)
            batch.create_foreign_key(f"fk_{table_name}_system_id", "assessment_systems", ["system_id"], ["id"], ondelete="CASCADE")

    _drop_single_unique(bind, "user_accounts", "username")
    _drop_single_unique(bind, "evaluator_directory", "name")
    _drop_single_unique(bind, "recruit_directory", "source_key")
    with op.batch_alter_table("user_accounts") as batch:
        batch.create_unique_constraint("uq_user_account_system_username", ["system_id", "username"])
    with op.batch_alter_table("evaluator_directory") as batch:
        batch.create_unique_constraint("uq_evaluator_directory_system_name", ["system_id", "name"])
    with op.batch_alter_table("recruit_directory") as batch:
        batch.create_unique_constraint("uq_recruit_directory_system_source", ["system_id", "source_key"])
    if sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    raise RuntimeError("Recruitment tenancy cannot be collapsed automatically without merging unrelated data.")
