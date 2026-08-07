from pathlib import Path


def test_frontend_assets_contain_the_two_workspaces():
    static = Path(__file__).parents[1] / "app" / "static"
    admin = (static / "admin.html").read_text(encoding="utf-8")
    evaluator = (static / "evaluator.html").read_text(encoding="utf-8")
    admin_js = (static / "admin.js").read_text(encoding="utf-8")
    evaluator_js = (static / "evaluator.js").read_text(encoding="utf-8")
    attendance = (static / "recruit_attendance.html").read_text(encoding="utf-8")
    attendance_js = (static / "recruit_attendance.js").read_text(encoding="utf-8")
    assert "Journee library" in admin
    assert "Rooms & assignments" in admin
    assert "Save attendance" in admin_js
    assert "Full Journee report" in admin_js
    assert "Results & profiles report" in admin_js
    assert "showSubmissionDetail" in admin_js
    assert "Edit evaluation" in admin_js
    assert "attendance-check" in admin_js
    assert "Type evaluator name and press Enter" in admin_js
    assert "evaluatorAttendanceSort" in admin_js
    assert "mandatorySearch" in admin_js
    assert 'class="active-check"' not in admin_js
    assert "data-photo-viewer" in admin_js
    assert "Evaluator login" in evaluator_js
    assert "/api/auth/login" in evaluator_js
    assert "Submit" in evaluator_js
    assert "sport-feedback" not in evaluator_js
    assert "Provisional Sport score" not in evaluator_js
    assert "criterion.weight" not in evaluator_js
    assert "durationPickerHtml" in evaluator_js
    assert 'type="number" min="0" max="5"' in evaluator_js
    common_js = (static / "common.js").read_text(encoding="utf-8")
    assert "wireDurationPickers" in common_js
    assert "data-duration-minutes" in common_js
    assert "Recruit attendance" in attendance
    assert "queueRecruitSave" in attendance_js
    assert "attendanceSave" not in attendance_js
    assert "checking for updates every 5 seconds" in attendance_js
    assert "attendance-photo-change" in attendance_js
    assert "removePhoto" in attendance_js
    assert "data-photo-viewer" in evaluator_js
    assert 'id="photoViewer"' in admin
    assert 'id="photoViewer"' in evaluator
    assert "viewport-fit=cover" in evaluator
    assert "?v=20260807.4" in admin
    assert "?v=20260807.4" in evaluator
    assert "common.js?v=20260807.4" in admin_js
    assert "common.js?v=20260807.4" in evaluator_js


def test_frontend_responses_prevent_stale_release_mixing(client):
    admin = client.get("/admin")
    evaluator = client.get("/evaluate")
    static_asset = client.get("/static/common.js")
    assert admin.headers["cache-control"] == "no-store"
    assert evaluator.headers["cache-control"] == "no-store"
    assert static_asset.headers["cache-control"] == "no-cache, max-age=0, must-revalidate"
