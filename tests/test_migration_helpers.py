from __future__ import annotations

import pytest

from scripts.migrate_postgres_to_cockroach import parent_first


def test_parent_first_orders_self_referenced_rows() -> None:
    rows = [
        {"id": "simulation", "reused_from_id": "skills"},
        {"id": "sport", "reused_from_id": None},
        {"id": "skills", "reused_from_id": None},
    ]

    ordered = parent_first(rows, id_key="id", parent_key="reused_from_id")

    assert [row["id"] for row in ordered].index("skills") < [row["id"] for row in ordered].index("simulation")


def test_parent_first_rejects_circular_relationships() -> None:
    rows = [
        {"id": "one", "parent": "two"},
        {"id": "two", "parent": "one"},
    ]

    with pytest.raises(RuntimeError, match="Circular"):
        parent_first(rows, id_key="id", parent_key="parent")
