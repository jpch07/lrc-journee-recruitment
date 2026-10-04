from pathlib import Path
import os

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parents[1]


def test_backup_ui_is_manual_and_uses_new_destination():
    source = (ROOT / 'app/static/sheet-backup.js').read_text(encoding='utf-8')
    admin = (ROOT / 'app/static/admin.js').read_text(encoding='utf-8')
    assert 'setInterval' not in source
    assert 'setTimeout' not in source  # No scheduled backup, no permanent polling.
    assert 'textContent' in source
    assert 'mountSheetBackup' in admin
    assert 'state.isOwner' in admin
    assert '11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0' in source


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('RUN_PLAYWRIGHT') != '1', reason='Browser opt-in')
def test_backup_ui_states_and_responsive_layout(tmp_path):
    source = (ROOT / 'app/static/sheet-backup.js').read_text(encoding='utf-8').replace('export function', 'function')
    css = (ROOT / 'app/static/styles.css').read_text(encoding='utf-8')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for width, name in [(1440, 'desktop'), (390, 'mobile')]:
            page.set_viewport_size({'width':width,'height':950})
            page.set_content('<main class="page-shell"><h1>Journee library</h1><div id="backup"></div></main>')
            page.add_style_tag(content=css)
            page.add_script_tag(content=source)
            page.evaluate('''() => {
              window.calls=[]; window.steps=0; window.failOnce=true;
              window.fakeApi=async (url, options) => {
                calls.push(url);
                if(url.endsWith('/start')) return {jobId:'example',state:'running',progress:0,total:2};
                if(url.endsWith('/advance')) {
                  if(failOnce) {failOnce=false; throw new Error('Connection interrupted. Retry safely.');}
                  steps++; return {jobId:'example',state:steps===2?'complete':'running',progress:steps,total:2,message:steps===2?'Backup complete.':'Uploading…'};
                }
                return {configured:true,connected:true,state:'ready',lastComplete:null};
              };
              mountSheetBackup(document.querySelector('#backup'),{api:fakeApi,mutation:()=>({method:'POST'})});
            }''')
            button = page.get_by_role('button', name='Back up workspace')
            button.wait_for()
            assert page.evaluate('calls') == ['/api/admin/sheet-backup']
            button.click()
            page.get_by_role('button', name='Retry upload').wait_for()
            page.get_by_role('button', name='Retry upload').click()
            page.get_by_text('Backup complete.', exact=True).wait_for()
            assert page.get_by_text('No completed backup verified yet.', exact=True).count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            screenshot = ROOT / '.impeccable/review' / f'sheet-backup-{name}.png'
            screenshot.parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(screenshot),full_page=True)
        browser.close()
