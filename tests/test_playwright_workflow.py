from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright


pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(os.getenv("RUN_PLAYWRIGHT") != "1", reason="Set RUN_PLAYWRIGHT=1 to run browser acceptance.")
def test_admin_create_and_mobile_layout(tmp_path):
    root = Path(__file__).parents[1]
    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "LRC_DATABASE_URL": f"sqlite:///{(tmp_path / 'browser.db').as_posix()}",
            "LRC_JOURNEE_ADMIN_PASSWORD": "browser-secret",
            "LRC_JOURNEE_SESSION_SECRET": "browser-session-secret-at-least-32-characters",
            "LRC_JOURNEE_COOKIE_SECURE": "false",
        }
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                if httpx.get(base + "/health/ready", timeout=1).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("Uvicorn did not become ready.")

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            page.goto(base + "/admin", wait_until="networkidle")
            page.fill('input[name="username"]', "JP Chaaya")
            page.fill('input[name="password"]', "browser-secret")
            page.click("#adminLoginSubmit")
            page.wait_for_selector("#createJourneyButton:visible")
            page.click("#createJourneyButton")
            page.fill('#createJourneyForm input[name="name"]', "Mobile Acceptance")
            page.click("#createJourneyForm button.primary")
            page.wait_for_selector("#workspaceView:not(.hidden)")
            assert page.locator("#journeyCrumb").inner_text() == "Mobile Acceptance"
            assert page.evaluate("document.documentElement.scrollWidth") == page.evaluate("window.innerWidth")
            page.evaluate("""async () => {
              const session = await fetch('/api/auth/session').then(response => response.json());
              const journeys = await fetch('/api/admin/journeys').then(response => response.json());
              const journey = journeys.find(item => item.name === 'Mobile Acceptance');
              const response = await fetch(`/api/admin/journeys/${journey.id}/recruits`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': session.csrfToken},
                body: JSON.stringify({name: 'Mobile Recruit'})
              });
              if (!response.ok) throw new Error(await response.text());
            }""")

            page.click("#menuButton")
            page.click('#workspaceNav button[data-section="attendance"]')
            page.click('.tabs button[data-tab="evaluators"]')
            page.wait_for_selector('#attendanceSearch[placeholder="Type evaluator name and press Enter"]')
            assert page.locator("#attendanceTable tbody tr").count() == 81
            page.fill("#attendanceSearch", "Wahle")
            page.press("#attendanceSearch", "Enter")
            page.fill("#attendanceSearch", "Andy")
            page.press("#attendanceSearch", "Enter")
            names = page.locator("#attendanceTable tbody tr td:first-child strong").all_inner_texts()
            assert names[:3] == ["Wahle", "Andy", "Ala2"]
            page.click("#saveAttendance")
            page.wait_for_selector("#saveAttendance:disabled")

            page.click("#menuButton")
            page.click('#workspaceNav button[data-section="assignments"]')
            page.click('.assignment-activity-tabs button[data-activity="escape_room"]')
            page.wait_for_selector("#mandatoryRooms")
            page.click("#mandatoryRooms")
            page.fill("#mandatorySearch", "Wahle")
            page.press("#mandatorySearch", "Enter")
            wahle_row = page.locator('.mandatory-row:has-text("Wahle")')
            assert wahle_row.locator("select").input_value() == "1"
            page.click("#mandatoryForm button.primary")
            page.wait_for_selector("#modal", state="hidden")
            page.click("#mandatoryRooms")
            wahle_row = page.locator('.mandatory-row:has-text("Wahle")')
            assert wahle_row.locator("select").input_value() == "1"
            page.click("#cancelModal")

            page.click("#menuButton")
            page.click('#workspaceNav button[data-section="settings"]')
            page.wait_for_selector("#recruitAttendanceLink")
            attendance_url = page.locator("#recruitAttendanceLink").input_value()
            assert attendance_url.startswith(base + "/lrc-journee-recruitment-2026/attendance/")

            attendance_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
            attendance_page = attendance_context.new_page()
            attendance_page.goto(attendance_url, wait_until="networkidle")
            attendance_page.fill('input[name="username"]', "JP Chaaya")
            attendance_page.fill('input[name="password"]', "browser-secret")
            attendance_page.click("#attendanceLoginSubmit")
            attendance_page.wait_for_selector("#attendanceAdd")
            assert attendance_page.locator("#attendanceSave").count() == 0
            recruit_card = attendance_page.locator('.attendance-recruit-card:has-text("Mobile Recruit")')
            recruit_card.wait_for()
            recruit_card.locator(".attendance-phone").fill("70123456")
            recruit_card.locator(".attendance-dob").fill("2004-03-02")
            recruit_card.locator(".attendance-present").check()
            attendance_page.wait_for_function("document.querySelector('#attendanceSyncStatus')?.textContent.includes('All changes saved')")
            recruit_card = attendance_page.locator('.attendance-recruit-card:has-text("Mobile Recruit")')
            assert recruit_card.locator(".attendance-arrival").input_value()
            attendance_page.locator(".attendance-summary h1").click()

            page.click("#menuButton")
            page.click('#workspaceNav button[data-section="attendance"]')
            page.click('.tabs button[data-tab="recruits"]')
            admin_row = page.locator('#attendanceTable tbody tr:has-text("Mobile Recruit")')
            admin_row.wait_for()
            assert page.locator("#saveAttendance").count() == 0
            admin_row.locator(".phone-input").fill("70999999")
            page.wait_for_timeout(900)
            assert admin_row.locator(".row-sync").inner_text() == "Saved"
            attendance_page.wait_for_function("document.querySelector('.attendance-recruit-card .attendance-phone')?.value === '70999999'")
            assert attendance_page.evaluate("document.documentElement.scrollWidth") == attendance_page.evaluate("window.innerWidth")

            viewer_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
            viewer_page = viewer_context.new_page()
            viewer_errors = []
            viewer_page.on('pageerror', lambda error: viewer_errors.append(str(error)))
            viewer_page.goto(base + "/lrc-journee-recruitment-2026/view", wait_until="networkidle")
            viewer_page.fill('#viewerLoginForm input[name="username"]', "JP Chaaya")
            viewer_page.fill('#viewerLoginForm input[name="password"]', "browser-secret")
            viewer_page.click("#viewerLoginSubmit")
            viewer_page.wait_for_selector('#viewerNav button[data-tab="results"]')
            journey_option = viewer_page.locator('#viewerJourney option:has-text("Mobile Acceptance")')
            viewer_page.eval_on_selector(
                "#viewerJourney",
                "(select, value) => { select.value = value; select.dispatchEvent(new Event('change', {bubbles: true})); }",
                journey_option.get_attribute("value"),
            )
            viewer_page.click("#viewerMenu")
            viewer_page.click('#viewerNav button[data-tab="profiles"]')
            viewer_page.wait_for_selector(".dimension-card")
            rank_summary = viewer_page.locator(".profile-rank-summary")
            assert "Overall rank" in rank_summary.inner_text()
            assert "Journee rank" in rank_summary.inner_text()
            assert "1 of 1" in rank_summary.inner_text()
            assert "completed Journees" not in rank_summary.inner_text()
            for width, label in [(390, "mobile"), (1280, "desktop")]:
                viewer_page.set_viewport_size({"width": width, "height": 844})
                assert viewer_page.evaluate("document.documentElement.scrollWidth") == width
                bounds = rank_summary.bounding_box()
                assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
                viewer_page.screenshot(path=str(tmp_path / f"management-ranks-{label}.png"))
            print(f"Management rank screenshots: {tmp_path}")
            viewer_page.set_viewport_size({"width": 390, "height": 844})
            assert viewer_page.locator("#viewerGeneralAssessmentForm input:not([disabled])").count() == 3
            assert viewer_page.locator("#viewerGeneralAssessmentForm textarea:not([disabled])").count() == 2
            assert viewer_page.locator("#viewerHost input:enabled, #viewerHost textarea:enabled").count() == 5
            viewer_page.fill('#viewerGeneralAssessmentForm input[name="factor:punctuality"]', "0.8")
            viewer_page.fill('#viewerGeneralAssessmentForm input[name="factor:respect"]', "0.9")
            viewer_page.fill('#viewerGeneralAssessmentForm input[name="factor:seriousness"]', "1")
            viewer_page.fill('#viewerGeneralAssessmentForm textarea[name="comment"]', "Management comment")
            viewer_page.fill('#viewerGeneralAssessmentForm textarea[name="notes"]', "Management note")
            viewer_page.locator('#viewerGeneralAssessmentForm textarea[name="notes"]').blur()
            viewer_page.wait_for_function("document.querySelector('#viewerProfileSaveStatus')?.textContent === 'Saved'")
            viewer_page.wait_for_function("document.querySelector('#viewerGeneralAssessmentForm textarea[name=notes]')?.value === 'Management note'")
            assessment_writes = []
            viewer_page.on("request", lambda request: assessment_writes.append(request.url) if request.method == "PUT" and request.url.endswith("/profile") else None)
            notes = viewer_page.locator('#viewerGeneralAssessmentForm textarea[name="notes"]')
            notes.focus(); notes.blur()
            viewer_page.evaluate("window.dispatchEvent(new Event('online')); window.dispatchEvent(new Event('online'))")
            viewer_page.wait_for_timeout(800)
            assert assessment_writes == [], "Unchanged focusout and online must not save"
            viewer_page.locator('.activity-card-button[data-activity="sport"]').click()
            viewer_page.locator('#managementCorrections > summary').click()
            viewer_page.locator('#correctionValue').fill('4')
            viewer_page.locator('#correctionReason').fill('Preserve this correction reason')
            viewer_page.route('**/corrections/preview', lambda route: route.fulfill(
                status=409, content_type='application/json', body='{"detail":"Changed elsewhere. Reload latest."}'))
            viewer_page.locator('#previewCorrection').click()
            viewer_page.wait_for_selector('#reloadCorrections:not([hidden])')
            assert viewer_page.locator('#correctionValue').input_value() == '4'
            assert viewer_page.locator('#correctionReason').input_value() == 'Preserve this correction reason'
            assert viewer_page.locator('#applyCorrection').is_hidden()
            viewer_page.unroute('**/corrections/preview')
            viewer_context.set_offline(True)
            viewer_page.locator('#previewCorrection').click()
            viewer_page.wait_for_function("document.querySelector('#correctionStatus').textContent.startsWith('Offline')")
            assert viewer_page.locator('#correctionValue').input_value() == '4'
            viewer_context.set_offline(False)
            viewer_page.locator('#previewCorrection').click()
            viewer_page.wait_for_selector('#correctionPreview:not(:empty)')
            assert 'does not create evaluator submissions' in viewer_page.locator('#correctionPreview').inner_text()
            for width, label in [(390, 'mobile'), (1280, 'desktop')]:
                viewer_page.set_viewport_size({'width': width, 'height': 844})
                viewer_page.locator('#managementCorrections').scroll_into_view_if_needed()
                assert viewer_page.evaluate('document.documentElement.scrollWidth') == width
                viewer_page.screenshot(path=str(tmp_path / f'management-corrections-{label}.png'))
            viewer_page.set_viewport_size({'width': 390, 'height': 844})
            viewer_page.locator('#applyCorrection').click()
            viewer_page.wait_for_selector('#viewerModal:not([open])', state='attached')
            viewer_page.wait_for_selector('.activity-card-button')
            viewer_page.locator('.activity-card-button[data-activity="escape_room"]').click()
            viewer_page.locator('#managementCorrections > summary').click()
            viewer_page.locator('input[name="correctionMode"][value="criteria"]').check()
            for field in viewer_page.locator('.criterion-correction-input').all():
                field.fill('4')
            viewer_page.locator('#previewCorrection').click()
            viewer_page.wait_for_selector('#correctionPreview:not(:empty)')
            viewer_page.locator('#applyCorrection').click()
            viewer_page.wait_for_selector('#viewerModal:not([open])', state='attached')
            viewer_page.locator('.activity-card-button[data-activity="escape_room"]').click()
            assert '4.00' in viewer_page.locator('.management-evaluation-score').inner_text()
            viewer_page.locator('#managementCorrections > summary').click()
            viewer_page.locator('#restoreCorrection').click()
            viewer_page.wait_for_selector('#correctionPreview:not(:empty)')
            viewer_page.locator('#applyCorrection').click()
            viewer_page.wait_for_selector('#viewerModal:not([open])', state='attached')
            viewer_page.locator('.activity-card-button[data-activity="sport"]').click()
            viewer_page.locator('#managementCorrections > summary').click()
            assert 'Effective: 4.00' in viewer_page.locator('#correctionCurrent').inner_text()
            assert 'JP Chaaya' in viewer_page.locator('.management-evaluation').inner_text()
            viewer_page.locator('input[name="correctionMode"][value="criteria"]').check()
            assert viewer_page.locator('#correctionCriteria').is_visible()
            viewer_page.locator('input[name="correctionMode"][value="single"]').check()
            viewer_page.locator('#restoreCorrection').click()
            viewer_page.wait_for_selector('#correctionPreview:not(:empty)')
            viewer_page.locator('#applyCorrection').click()
            viewer_page.wait_for_selector('#viewerModal:not([open])', state='attached')
            viewer_page.wait_for_selector('.activity-card-button')
            viewer_page.locator('.edit-color-correction').click()
            viewer_page.locator('#managementCorrections > summary').click()
            viewer_page.locator('#correctionColor').select_option('green')
            viewer_page.locator('#previewCorrection').click()
            viewer_page.wait_for_selector('#correctionPreview:not(:empty)')
            viewer_page.locator('#applyCorrection').click()
            viewer_page.wait_for_selector('#viewerModal:not([open])', state='attached')
            viewer_page.wait_for_selector('.activity-card-button')
            assert 'Manual' in viewer_page.locator('.grade-orb small').inner_text()
            viewer_page.locator('.edit-color-correction').click()
            viewer_page.locator('#managementCorrections > summary').click()
            viewer_page.locator('#restoreCorrection').click()
            viewer_page.wait_for_selector('#correctionPreview:not(:empty)')
            viewer_page.locator('#applyCorrection').click()
            viewer_page.wait_for_selector('#viewerModal:not([open])', state='attached')
            viewer_page.wait_for_selector('.activity-card-button')
            viewer_page.locator(".dimension-card").first.click()
            viewer_page.wait_for_selector("#viewerModal[open]")
            viewer_page.click("#closeViewerModal")
            viewer_page.click("#viewerMenu")
            viewer_page.click('#viewerNav button[data-tab="attendance"]')
            viewer_page.wait_for_selector('table:has-text("Mobile Recruit")')
            assert viewer_page.locator("#viewerHost input:not([disabled]), #viewerHost textarea:not([disabled])").count() == 0
            assert viewer_page.evaluate("document.documentElement.scrollWidth") == viewer_page.evaluate("window.innerWidth")
            viewer_page.click("#viewerMenu")
            viewer_page.click('#viewerNav button[data-tab="results"]')
            viewer_page.wait_for_selector("#viewerHost table")
            assert "Journee rank" not in viewer_page.locator("#viewerHost thead").inner_text()
            assert [text.casefold() for text in viewer_page.locator("#viewerHost th").all_inner_texts()[:3]] == ["color", "rank", "recruit"]
            rank_cell = viewer_page.locator("#viewerHost tbody tr").first.locator("td").nth(1)
            # This Journee is not completed: no workspace rank yet, and no
            # misleading fallback to the removed Journee-rank column.
            assert rank_cell.locator('[aria-label="Not ranked"]').count() == 1
            assert rank_cell.locator(".rank-number").count() == 0
            assert viewer_page.locator('[aria-label="Not ranked"]').count() == 1
            for width, label in [(390, "mobile"), (1280, "desktop")]:
                viewer_page.set_viewport_size({"width": width, "height": 844})
                assert viewer_page.evaluate("document.documentElement.scrollWidth") == width
                viewer_page.screenshot(path=str(tmp_path / f"management-rank-table-{label}.png"))
            assert viewer_errors == []
            viewer_context.close()
            attendance_context.close()

            admin_errors = []
            page.on('pageerror', lambda error: admin_errors.append(str(error)))
            page.click('#menuButton')
            page.click('#workspaceNav button[data-section="profiles"]')
            page.locator('.profile-activity-button[data-activity-code="sport"]').click()
            page.locator('#managementCorrections > summary').click()
            page.locator('#correctionValue').fill('5')
            page.locator('#previewCorrection').click()
            page.wait_for_selector('#correctionPreview:not(:empty)')
            assert page.locator('.correction-equivalents').count() == 1
            page.locator('#applyCorrection').click()
            page.wait_for_selector('#modal:not([open])', state='attached')
            page.locator('.profile-activity-button[data-activity-code="sport"]').click()
            assert '5.00' in page.locator('.management-evaluation-score').inner_text()
            assert 'JP Chaaya' in page.locator('.management-evaluation').inner_text()
            page.locator('#cancelModal').click()
            assert admin_errors == []

            neutral_context = browser.new_context(viewport={"width": 1280, "height": 820})
            neutral_page = neutral_context.new_page()
            neutral_page.goto(base + "/", wait_until="networkidle")
            neutral_page.click("#signupTab")
            neutral_page.fill('#platformSignupForm input[name="username"]', "Neutral Owner")
            neutral_page.fill('#platformSignupForm input[name="password"]', "neutral-password")
            neutral_page.click('#platformSignupForm button[type="submit"]')
            neutral_page.wait_for_selector("#workspaceScreen:not(.hidden)")
            neutral_page.click("#showCreateWorkspace")
            neutral_page.fill("#workspaceName", "Neutral Browser Workspace")
            neutral_page.click('#createWorkspaceForm button[type="submit"]')
            card = neutral_page.locator('.platform-workspace-card:has-text("Neutral Browser Workspace")')
            card.wait_for()
            card.locator(".platform-workspace-main").click()
            neutral_page.wait_for_url("**/neutral-browser-workspace/admin")
            neutral_page.goto(base + "/neutral-browser-workspace/configure", wait_until="networkidle")
            neutral_page.wait_for_selector("#configApp:not(.hidden)")
            assert neutral_page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--red').trim()"
            ) == "#4f46e5"
            assert neutral_page.locator(".config-heading .eyebrow").evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ) == "rgb(79, 70, 229)"
            neutral_page.locator("#configPublish").hover()
            assert neutral_page.locator("#configPublish").evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ) != "rgb(173, 9, 39)"

            neutral_page.goto(base + "/", wait_until="networkidle")
            delete_button = neutral_page.locator(
                '.platform-workspace-card:has-text("Neutral Browser Workspace") .platform-workspace-delete'
            )
            neutral_page.once("dialog", lambda dialog: dialog.accept("Neutral Browser Workspace"))
            delete_button.click()
            neutral_page.wait_for_function(
                "!document.querySelector('.platform-workspace-card')?.textContent.includes('Neutral Browser Workspace')"
            )
            neutral_context.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
