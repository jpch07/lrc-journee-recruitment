from decimal import Decimal

import pytest

from app.assessment_runtime import active_assessment_definition
from app.management_corrections import build_override_map, correction_signature, normalize_target, resolve_targets


def test_uniform_target_uses_every_contributing_criterion():
    definition = active_assessment_definition()
    activity = next(a for a in definition.activities if a.enabled)
    changed = build_override_map(definition, {}, 'activity', activity.key, Decimal('4'))
    assert set(changed[activity.key]) == {c.key for c in activity.criteria if c.weight > 0}
    assert set(changed[activity.key].values()) == {'0.8'}


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-0.1', '5.1'])
def test_invalid_targets(value):
    with pytest.raises(ValueError):
        normalize_target(Decimal(value), Decimal(0), Decimal(5))


def test_scale_and_no_input_step_rounding():
    assert normalize_target(Decimal('2.25'), Decimal(1), Decimal(6)) == Decimal('.25')


def test_signature_ignores_labels_not_scoring():
    definition = active_assessment_definition().model_copy(deep=True)
    before = correction_signature(definition)
    definition.activities[0].criteria[0].name = 'Renamed'
    assert correction_signature(definition) == before
    definition.activities[0].criteria[0].maximum += 1
    assert correction_signature(definition) != before


def test_unknown_target_and_copy_isolation():
    definition = active_assessment_definition()
    with pytest.raises(ValueError):
        resolve_targets(definition, 'activity', 'no_such_activity')
    activity = definition.activities[0]
    original = {activity.key: {activity.criteria[0].key: '0.2'}}
    changed = build_override_map(definition, original, 'activity', activity.key, Decimal(4))
    assert original[activity.key][activity.criteria[0].key] == '0.2'
    assert changed[activity.key][activity.criteria[0].key] == '0.8'


def test_migration_is_additive_and_resumable(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        for table in ['assessment_systems', 'journeys', 'recruits']:
            conn.execute(text(f'CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY)'))
        conn.execute(text("INSERT INTO recruits VALUES ('unchanged')"))
        spec = importlib.util.spec_from_file_location('correction_migration', Path(__file__).parents[1] / 'migrations/versions/0019_management_corrections.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, 'op', Operations(MigrationContext.configure(conn)))
        module.upgrade()
        module.upgrade()
        assert 'management_corrections' in inspect(conn).get_table_names()
        assert conn.execute(text('SELECT id FROM recruits')).scalar() == 'unchanged'
        assert conn.execute(text('SELECT count(*) FROM management_corrections')).scalar() == 0


def test_dimension_display_scale_and_target_scale():
    definition = active_assessment_definition().model_copy(deep=True)
    dimension = definition.dimensions[0]
    dimension.displayMaximum = Decimal(10)
    result = build_override_map(definition, {}, 'dimension', dimension.key, Decimal(8))
    assert {v for c in result.values() for v in c.values()} == {'0.8'}
    activity = next(a for a in definition.activities if a.scoring == 'target_average')
    result = build_override_map(definition, {}, 'criterion', activity.criteria[0].key, Decimal(4), activity.key)
    assert result[activity.key][activity.criteria[0].key] == '0.8'
