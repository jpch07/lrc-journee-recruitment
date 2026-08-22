from pathlib import Path


def test_frontend_assets_contain_the_application_workspaces():
    static = Path(__file__).parents[1] / "app" / "static"
    home = (static / "home.html").read_text(encoding="utf-8")
    admin = (static / "admin.html").read_text(encoding="utf-8")
    evaluator = (static / "evaluator.html").read_text(encoding="utf-8")
    admin_js = (static / "admin.js").read_text(encoding="utf-8")
    evaluator_js = (static / "evaluator.js").read_text(encoding="utf-8")
    attendance = (static / "recruit_attendance.html").read_text(encoding="utf-8")
    attendance_js = (static / "recruit_attendance.js").read_text(encoding="utf-8")
    viewer = (static / "viewer.html").read_text(encoding="utf-8")
    viewer_js = (static / "viewer.js").read_text(encoding="utf-8")
    assert "Journee library" in admin
    assert "Rooms & assignments" in admin
    assert "Save attendance" in admin_js
    assert "Interactive management report" in admin_js
    assert "showSubmissionDetail" in admin_js
    assert "Edit evaluation" in admin_js
    assert "row.generalComment" in admin_js
    assert "results-comment-cell" in admin_js
    assert 'Dimension averages /5' in admin_js
    assert 'Grade /5' in admin_js
    assert 'dimensionGrade(item.score)' in admin_js
    assert "attendance-check" in admin_js
    assert "Type evaluator name and press Enter" in admin_js
    assert "evaluatorAttendanceSort" in admin_js
    assert "mandatorySearch" in admin_js
    assert "permissionsSearch" in admin_js
    assert "WhatsApp present evaluators" in admin_js
    assert "Edit info" in admin_js
    assert "delete-account" in admin_js
    assert "Username: ${account.username}\\nPassword: ${account.managedPassword}" in admin_js
    assert "item.fullName" in admin_js
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
    assert "wireAccountPicker" in common_js
    assert "item.fullName" in common_js
    assert "wireRecruitDirectoryPicker" in common_js
    assert "selectedAccount" in common_js
    assert "data-duration-minutes" in common_js
    assert "Participant attendance" in attendance
    assert "queueRecruitSave" in attendance_js
    assert "attendanceSave" not in attendance_js
    assert "checking for updates every 5 seconds" in attendance_js
    assert "attendance-photo-change" in attendance_js
    assert "/api/recruit-attendance/recruits/from-directory" in attendance_js
    assert "Only administrators can add someone who is not in the master recruit list." in attendance_js
    assert "removePhoto" in attendance_js
    assert "data-photo-viewer" in evaluator_js
    assert "Search your name" in admin
    assert "Search your name" in evaluator_js
    assert "Search your name" in attendance_js
    assert "Management view" in viewer
    assert "General assessment editable" in viewer
    assert "viewerGeneralAssessmentForm" in viewer_js
    assert "viewerProfileSaveStatus" in viewer_js
    assert 'setStatus("Saved", "saved")' in viewer_js
    assert "View criteria" in viewer_js
    assert "View evaluations" in viewer_js
    assert "Evaluator breakdown" in viewer_js
    assert "row.generalComment" in viewer_js
    assert "results-comment-cell" in viewer_js
    assert 'Grade /5' in viewer_js
    assert 'dimensionGrade(item.score)' in viewer_js
    assert "Download interactive Excel report" not in viewer_js
    assert "Download Excel Report" in viewer
    assert "All completed Journees" in viewer_js
    assert "viewer.js?v=20260820.3" in viewer
    assert "platformLoginForm" in home
    assert "platformSignupForm" in home
    assert "workspaceList" in home
    assert "Recruitment ID" not in home
    assert 'id="photoViewer"' in admin
    assert 'id="photoViewer"' in evaluator
    assert "viewport-fit=cover" in evaluator
    assert "admin.js?v=20260820.3" in admin
    assert "?v=20260820.3" in evaluator
    assert "common.js?v=20260810.1" in admin_js
    assert "common.js?v=20260810.1" in evaluator_js


def test_static_workspace_shells_are_neutral_before_configuration_loads():
    static = Path("app/static")
    for filename in ("admin.html", "evaluator.html", "viewer.html", "recruit_attendance.html"):
        content = (static / filename).read_text(encoding="utf-8")
        assert "Lebanese Red Cross" not in content
        assert ">LRC<" not in content
    configurator = (static / "configurator.js").read_text(encoding="utf-8")
    assert "Use LRC preset" not in configurator


def test_frontend_responses_prevent_stale_release_mixing(client):
    admin = client.get("/admin")
    evaluator = client.get("/evaluate")
    static_asset = client.get("/static/common.js")
    assert admin.headers["cache-control"] == "no-store"
    assert evaluator.headers["cache-control"] == "no-store"
    assert static_asset.headers["cache-control"] == "no-cache, max-age=0, must-revalidate"
