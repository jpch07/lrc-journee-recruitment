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
            assert viewer_page.locator("#viewerGeneralAssessmentForm input:not([disabled])").count() == 3
            assert viewer_page.locator("#viewerGeneralAssessmentForm textarea:not([disabled])").count() == 2
            assert viewer_page.locator("#viewerHost input:not([disabled]), #viewerHost textarea:not([disabled])").count() == 5
            viewer_page.fill('#viewerGeneralAssessmentForm input[name="punctuality"]', "0.8")
            viewer_page.fill('#viewerGeneralAssessmentForm input[name="respect"]', "0.9")
            viewer_page.fill('#viewerGeneralAssessmentForm input[name="seriousness"]', "1")
            viewer_page.fill('#viewerGeneralAssessmentForm textarea[name="comment"]', "Management comment")
            viewer_page.fill('#viewerGeneralAssessmentForm textarea[name="notes"]', "Management note")
            viewer_page.click("#viewerSaveAssessment")
            viewer_page.wait_for_function("document.querySelector('#viewerGeneralAssessmentForm textarea[name=notes]')?.value === 'Management note'")
            viewer_page.locator(".dimension-card").first.click()
            viewer_page.wait_for_selector("#viewerModal[open]")
            viewer_page.click("#closeViewerModal")
            viewer_page.click("#viewerMenu")
            viewer_page.click('#viewerNav button[data-tab="attendance"]')
            viewer_page.wait_for_selector('table:has-text("Mobile Recruit")')
            assert viewer_page.locator("#viewerHost input:not([disabled]), #viewerHost textarea:not([disabled])").count() == 0
            assert viewer_page.evaluate("document.documentElement.scrollWidth") == viewer_page.evaluate("window.innerWidth")
            viewer_context.close()
            attendance_context.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
