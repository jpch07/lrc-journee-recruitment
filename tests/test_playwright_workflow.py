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
            page.fill('input[name="displayName"]', "Browser Admin")
            page.fill('input[name="password"]', "browser-secret")
            page.click("#loginForm button")
            page.wait_for_selector("#createJourneyButton:visible")
            page.click("#createJourneyButton")
            page.fill('#createJourneyForm input[name="name"]', "Mobile Acceptance")
            page.click("#createJourneyForm button.primary")
            page.wait_for_selector("#workspaceView:not(.hidden)")
            assert page.locator("#journeyCrumb").inner_text() == "Mobile Acceptance"
            assert page.evaluate("document.documentElement.scrollWidth") == page.evaluate("window.innerWidth")

            page.click("#menuButton")
            page.click('#workspaceNav button[data-section="attendance"]')
            page.click('.tabs button[data-tab="evaluators"]')
            page.wait_for_selector('#attendanceSearch[placeholder="Type evaluator name and press Enter"]')
            assert page.locator("#attendanceTable tbody tr").count() == 82
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
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
