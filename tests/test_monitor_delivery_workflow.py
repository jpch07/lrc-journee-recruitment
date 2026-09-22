from pathlib import Path
import textwrap
import pytest
from scripts import event_day_guard as guard


def test_delivery_test_is_manual_bounded_and_uses_only_saved_secrets():
    workflow = (Path(__file__).parents[1] / ".github/workflows/test-monitor-email.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "timeout-minutes: 5" in workflow
    assert "contents: read" in workflow
    for name in ("HOST", "PORT", "USERNAME", "PASSWORD"):
        assert "secrets.EVALDAY_SMTP_" + name in workflow
    assert "secrets.EVALDAY_ALERT_FROM" in workflow
    assert "secrets.EVALDAY_ALERT_TO" in workflow
    assert "outage-recovery-test.invalid" in workflow
    assert "monitor.observe(False)" in workflow
    assert "monitor.observe(True)" in workflow
    assert 'assert delivered == ["outage", "healthy"]' in workflow
    assert "Inbox delivery has not been verified" in workflow


@pytest.fixture(autouse=True)
def clean_database():
    yield


@pytest.mark.parametrize("outcomes,expected_calls,success", [
    ([True, True], ["outage", "healthy"], True),
    ([False], ["outage"], False),
    ([True, False], ["outage", "healthy"], False),
])
def test_delivery_workflow_runtime(monkeypatch, outcomes, expected_calls, success):
    workflow = (Path(__file__).parents[1] / ".github/workflows/test-monitor-email.yml").read_text()
    program = textwrap.dedent(workflow.split("python - <<'PY'\n", 1)[1].rsplit("          PY", 1)[0])
    calls = []
    answers = iter(outcomes)
    monkeypatch.setattr(guard, "email_settings", lambda: object())
    def send(configuration, app_url, state):
        assert app_url == "https://outage-recovery-test.invalid"
        calls.append(state)
        return next(answers)
    monkeypatch.setattr(guard, "send_email_alert", send)
    if success:
        exec(compile(program, "<workflow-test>", "exec"), {})
    else:
        with pytest.raises((AssertionError, SystemExit)):
            exec(compile(program, "<workflow-test>", "exec"), {})
    assert calls == expected_calls
