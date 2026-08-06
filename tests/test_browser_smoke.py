from pathlib import Path


def test_frontend_assets_contain_the_two_workspaces():
    static = Path(__file__).parents[1] / "app" / "static"
    admin = (static / "admin.html").read_text(encoding="utf-8")
    evaluator = (static / "evaluator.html").read_text(encoding="utf-8")
    admin_js = (static / "admin.js").read_text(encoding="utf-8")
    evaluator_js = (static / "evaluator.js").read_text(encoding="utf-8")
    assert "Journee library" in admin
    assert "Rooms & assignments" in admin
    assert "Save attendance" in admin_js
    assert "Full Excel" in admin_js
    assert "attendance-check" in admin_js
    assert 'class="active-check"' not in admin_js
    assert "data-photo-viewer" in admin_js
    assert "Select your name" in evaluator_js
    assert "Submit" in evaluator_js
    assert "sport-feedback" not in evaluator_js
    assert "Provisional Sport score" not in evaluator_js
    assert "criterion.weight" not in evaluator_js
    assert "data-photo-viewer" in evaluator_js
    assert 'id="photoViewer"' in admin
    assert 'id="photoViewer"' in evaluator
    assert "viewport-fit=cover" in evaluator
