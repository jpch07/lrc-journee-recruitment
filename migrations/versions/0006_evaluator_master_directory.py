"""import the authoritative 2026 evaluator directory

Revision ID: 0006_evaluator_master_directory
Revises: 0005_event_day_protection
"""

from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa

from app.evaluator_master import BASE_EVALUATORS


revision = "0006_evaluator_master_directory"
down_revision = "0005_event_day_protection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    directory = sa.Table("evaluator_directory", metadata, autoload_with=connection)
    journeys = sa.Table("journeys", metadata, autoload_with=connection)
    evaluators = sa.Table("evaluators", metadata, autoload_with=connection)
    now = datetime.now(timezone.utc)
    system_id = None
    if "system_id" in directory.c:
        systems = sa.Table("assessment_systems", metadata, autoload_with=connection)
        system_id = connection.execute(sa.select(systems.c.id).limit(1)).scalar()

    connection.execute(directory.update().values(active=False))
    master: list[tuple[str, str, str]] = []
    for name, role in BASE_EVALUATORS:
        existing = connection.execute(
            sa.select(directory.c.id).where(sa.func.lower(directory.c.name) == name.lower())
        ).first()
        if existing:
            directory_id = existing.id
            connection.execute(
                directory.update().where(directory.c.id == directory_id).values(
                    name=name,
                    default_role=role,
                    active=True,
                )
            )
        else:
            directory_id = str(uuid.uuid4())
            values = {
                "id": directory_id,
                "name": name,
                "default_role": role,
                "active": True,
                "created_at": now,
            }
            if "system_id" in directory.c:
                values["system_id"] = system_id
            connection.execute(directory.insert().values(**values))
        master.append((directory_id, name, role))

    editable_journeys = connection.execute(
        sa.select(journeys.c.id).where(journeys.c.status.in_(("draft", "ready")))
    ).all()
    master_names = {name.casefold() for _, name, _ in master}
    for journey_row in editable_journeys:
        current = connection.execute(
            sa.select(evaluators).where(evaluators.c.journey_id == journey_row.id)
        ).mappings().all()
        by_name = {item["name"].casefold(): item for item in current}
        for item in current:
            if item["name"].casefold() not in master_names:
                connection.execute(
                    evaluators.update().where(evaluators.c.id == item["id"]).values(active=False, present=False)
                )
        for directory_id, name, role in master:
            item = by_name.get(name.casefold())
            if item:
                connection.execute(
                    evaluators.update().where(evaluators.c.id == item["id"]).values(
                        directory_id=directory_id,
                        name=name,
                        role=role,
                        active=True,
                        updated_at=now,
                    )
                )
            else:
                connection.execute(
                    evaluators.insert().values(
                        id=str(uuid.uuid4()),
                        journey_id=journey_row.id,
                        directory_id=directory_id,
                        name=name,
                        role=role,
                        present=False,
                        active=True,
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )


def downgrade() -> None:
    # Historical and custom evaluator records must never be deleted on downgrade.
    pass
