"""Real browser flows on fictional linked fixture, with optional simulated exchange delay."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest
from playwright.sync_api import sync_playwright
from test_sitewide_performance import seed
from app.db import SessionLocal
from app.models import AssignmentRound, AuditEvent, Recruit
from sqlalchemy import select

pytestmark = pytest.mark.browser

@pytest.mark.skipif(os.getenv("RUN_PLAYWRIGHT") != "1", reason="Browser opt-in")
def test_sitewide_click_flows(client, tmp_path):
    j = seed(client)
    # The profile fixture includes an empty, noncurrent preview. Start this
    # editor regression with the real published assignments instead.
    with SessionLocal() as db:
        for preview in db.scalars(select(AssignmentRound).where(
            AssignmentRound.journey_id == j,
            AssignmentRound.activity_code == "escape_room",
            AssignmentRound.status == "preview",
        )):
            db.delete(preview)
        db.commit()
        recruit = db.scalar(select(Recruit).where(Recruit.journey_id == j))
        from app.utils import dumps
        db.add(AuditEvent(journey_id=j, actor_type='admin', actor_name='Audit test manager',
            action='management.correction', entity_type='recruit', entity_id=recruit.id,
            before_json=dumps({'overallScore': 3.2512345}),
            after_json=dumps({'overallScore': 4, 'operation': {'level': 'activity', 'key': 'skills', 'action': 'set'},
                'affectedCriteria': [{'activityKey': 'skills', 'key': 'posture',
                    'before': {'name': 'Posture and lifting technique', 'effectiveAverage': 3, 'maximum': 5},
                    'after': {'name': 'Posture and lifting technique', 'effectiveAverage': 4, 'maximum': 5}}]})))
        db.commit()
    root = Path(os.getenv("LRC_BASELINE_APP_DIR", Path(__file__).parents[1]))
    baseline = bool(os.getenv("LRC_BASELINE_APP_DIR"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    delay = float(os.getenv("LRC_SIMULATED_EXCHANGE_MS", "0"))/1000
    wrapper = tmp_path / "local_probe.py"
    wrapper.write_text("import time\nfrom sqlalchemy import event\nfrom app.main import app\nfrom app.db import engine\ndef pause(*args): time.sleep("+repr(delay)+")\nevent.listen(engine, 'before_cursor_execute', pause)\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)+os.pathsep+str(tmp_path)
    log = (tmp_path/"server.log").open("w")
    server = subprocess.Popen([sys.executable,"-m","uvicorn","local_probe:app","--host","127.0.0.1","--port",str(port)],cwd=root,env=env,stdout=log,stderr=log)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base+"/health/live",timeout=1).status_code == 200: break
            except httpx.HTTPError: pass
            time.sleep(.1)
        else: pytest.fail((tmp_path/"server.log").read_text())
        with sync_playwright() as pw:
            try: browser = pw.chromium.launch(headless=True)
            except Exception: browser = pw.chromium.launch(channel="msedge",headless=True)
            context = browser.new_context()
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base+"/admin",wait_until="networkidle")
            page.fill('input[name="username"]',"JP Chaaya")
            page.fill('input[name="password"]',"test-password")
            page.click("#adminLoginSubmit")
            page.wait_for_selector(f'article[data-id="{j}"] .open-journey')
            requests = []
            page.on("request", lambda request: requests.append((request.method,request.url)) if "/api/admin/journeys/" in request.url else None)
            started = time.perf_counter()
            page.click(f'article[data-id="{j}"] .open-journey')
            page.wait_for_selector('#refreshDashboard')
            page.wait_for_function("document.querySelector('#workspaceNav button[data-section=dashboard]')?.classList.contains('active') && !document.querySelector('#sectionHost .loading-card')")
            opening = {"ms":round((time.perf_counter()-started)*1000,1),"requests":len(requests)}
            requests.clear();started=time.perf_counter()
            page.click('#workspaceNav button[data-section="assignments"]')
            page.wait_for_selector('#activityAvailability')
            initial_load={"ms":round((time.perf_counter()-started)*1000,1),"requests":len(requests)}
            requests.clear();started=time.perf_counter()
            page.click('.assignment-activity-tabs button[data-activity="escape_room"]')
            page.wait_for_selector('#editPublishedRooms')
            loading={"ms":round((time.perf_counter()-started)*1000,1),"requests":len(requests)}
            requests.clear();started=time.perf_counter()
            page.click('#editPublishedRooms')
            page.wait_for_selector('#applyRoomChanges')
            editing={"ms":round((time.perf_counter()-started)*1000,1),"requests":len(requests)}
            assert editing["requests"] == (7 if baseline else 1)
            if not baseline:
                selector = page.locator('.room-move[data-type="recruit"]').first
                selector.select_option('2')
                page.once('dialog', lambda dialog: dialog.dismiss())
                page.click('.assignment-activity-tabs button[data-activity="sport"]')
                assert page.locator('.assignment-activity-tabs button.active').get_attribute('data-activity') == 'escape_room'
                assert selector.input_value() == '2'
                page.click('#saveRoomMoves')
                page.wait_for_function("!document.querySelector('#saveRoomMoves')?.disabled")

            if not baseline:
                # Both editors may contain drafts. Saving either must preserve
                # the other's DOM and base revision; Apply must not publish it.
                if page.locator('#editPublishedAssignments').count():
                    page.click('#editPublishedAssignments')
                page.wait_for_selector('#saveAssignmentEdits')
                rows = page.locator('.assignment-row').count()
                assert rows >= 2
                selector = page.locator('.room-move[data-type="recruit"]').first
                selector.select_option('1')
                page.locator('.remove-assignment').first.click()
                requests.clear()
                page.click('#applyRoomChanges'); page.click('#applyAssignments')
                page.wait_for_timeout(100)
                assert not [r for r in requests if r[0] == 'POST'], 'Apply must reject unsaved drafts locally'
                revision = int(page.locator('#workingRoomRevision').inner_text())
                page.click('#saveRoomMoves')
                page.wait_for_function("document.querySelector('#workingRoomRevision')?.textContent === '"+str(revision+1)+"'")
                assert page.locator('.assignment-row').count() == rows-1
                # Saving the same editor again must use the acknowledged revision.
                selector.select_option('2')
                page.click('#saveRoomMoves')
                page.wait_for_function("document.querySelector('#workingRoomRevision')?.textContent === '"+str(revision+2)+"'")
                assert page.locator('.assignment-row').count() == rows-1
                page.click('#saveAssignmentEdits')
                page.wait_for_function("!document.querySelector('#saveAssignmentEdits')?.disabled")
                assert page.locator('.assignment-row').count() == rows-1
                assert page.locator('.room-move[data-type="recruit"]').first.input_value() == '2'
                # Reverse the acknowledgement order as well.
                page.locator('.room-move[data-type="recruit"]').first.select_option('1')
                page.locator('.remove-assignment').first.click()
                revision = int(page.locator('#workingAssignmentRevision').inner_text())
                page.click('#saveAssignmentEdits')
                page.wait_for_function("document.querySelector('#workingAssignmentRevision')?.textContent === '"+str(revision+1)+"'")
                assert page.locator('.room-move[data-type="recruit"]').first.input_value() == '1'
                page.click('#saveRoomMoves')
                page.wait_for_function("!document.querySelector('#saveRoomMoves')?.disabled")
                assert page.locator('.assignment-row').count() == rows-2

            # Force old activity responses to finish after a newer selection.
            if not baseline:
                page.route('**/activities/sport/workspace', lambda route: (time.sleep(.15), route.continue_()))
                page.click('.assignment-activity-tabs button[data-activity="sport"]')
                page.click('.assignment-activity-tabs button[data-activity="escape_room"]')
                page.wait_for_selector('#applyRoomChanges')
                page.wait_for_timeout(300)
                assert page.locator('.assignment-activity-tabs button.active').get_attribute('data-activity') == 'escape_room'
            # Management must keep the full roster/profile behavior while
            # eliminating the library -> completed-view request waterfall.
            management = page.context.new_page()
            management_requests = []
            management.on("request", lambda request: management_requests.append(request.url) if "/api/view/" in request.url else None)
            management.goto(base+"/view", wait_until="networkidle")
            management.wait_for_selector('#viewerHost tbody tr')
            assert len([url for url in management_requests if url.endswith('/bootstrap')]) == 1
            assert not [url for url in management_requests if url.endswith('/completed') or url.endswith('/journeys')]
            management.click('#viewerNav button[data-tab="attendance"]')
            management.wait_for_selector('#viewerHost h1')
            assert management.locator('#viewerHost h1').inner_text() == 'Attendance'
            assert management.locator('#viewerHost tbody tr').count() > 0
            management.click('#viewerNav button[data-tab="profiles"]')
            management.wait_for_selector('#viewerGeneralAssessmentForm textarea[name="comment"]')
            management.click('#viewerNav button[data-tab="results"]')
            management.wait_for_selector('#viewerHost tbody tr')
            management.select_option('#viewerJourney', j)
            management.wait_for_load_state('networkidle')
            held = []
            def hold_completed(route):
                response = route.fetch()
                held.append((route, response))
            management.route('**/api/view/completed', hold_completed)
            management.select_option('#viewerJourney', 'completed')
            management.wait_for_function("document.querySelector('#viewerJourney').value === 'completed'")
            # Pump browser events until the old completed response is retained.
            for _ in range(50):
                if held: break
                management.wait_for_timeout(20)
            assert held
            management.select_option('#viewerJourney', j)
            management.wait_for_timeout(200)
            held[0][0].fulfill(response=held[0][1])
            management.wait_for_load_state('networkidle')
            assert management.locator('#viewerJourney').input_value() == j
            assert 'Shared rankings across every completed Journee.' not in management.locator('#viewerHost').inner_text()
            management.close()
            page.click('#libraryButton')
            page.wait_for_selector('#libraryAuditButton')
            page.click('#libraryAuditButton')
            page.wait_for_selector('.audit-entry')
            assert not page.locator('#sidebar').is_visible()
            page.fill('#auditFilters input[name="search"]', 'Audit test manager')
            page.click('#auditFilters button[type="submit"]')
            page.wait_for_function("document.querySelector('#auditStatus')?.textContent.includes('1 event shown')")
            assert page.locator('.audit-person').inner_text()
            assert '3.25' in page.locator('#auditFeed').inner_text()
            assert 'configurationSignature' not in page.locator('#auditFeed').inner_text()
            page.click('.audit-details summary')
            assert page.locator('.audit-details .audit-change').first.is_visible()
            assert 'Posture and lifting technique' in page.locator('.audit-details').inner_text()
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.screenshot(path=str(tmp_path / 'audit-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(tmp_path / 'audit-mobile.png'), full_page=True)
            print('AUDIT_SCREENSHOTS', tmp_path)
            page.reload(wait_until='networkidle')
            page.wait_for_selector('#auditFilters')
            page.click('#auditBack')
            page.wait_for_selector('#libraryAuditButton')
            assert not errors, errors
            print("BROWSER_FLOW", {"baseline":baseline,"simulated_exchange_ms":delay*1000,"open":opening,"initial_activity":initial_load,"activity_switch":loading,"click_to_editable":editing})
            browser.close()
    finally:
        server.terminate()
        try: server.wait(timeout=10)
        except subprocess.TimeoutExpired: server.kill();server.wait()
        log.close()
