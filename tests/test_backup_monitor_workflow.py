from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_database():
    yield


def test_backup_monitor_uses_separate_target_and_state_with_same_protection():
    root = Path(__file__).parents[1] / '.github/workflows'
    original = (root / 'event-day-watchdog.yml').read_text()
    backup = (root / 'backup-event-day-watchdog.yml').read_text()
    expected = original.replace('name: Event-day protection', 'name: Backup event-day protection', 1)
    expected = expected.replace('Journee protection $', 'Backup Journee protection $')
    expected = expected.replace('vars.RENDER_APP_URL', 'vars.RENDER_BACKUP_APP_URL')
    expected = expected.replace('monitor-baseline-v1-', 'backup-monitor-baseline-v1-')
    expected = expected.replace('monitor-primary-v1-', 'backup-monitor-primary-v1-')
    assert backup == expected
    assert 'vars.RENDER_APP_URL' not in backup
    assert 'vars.RENDER_BACKUP_APP_URL' in backup
