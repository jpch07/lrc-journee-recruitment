"""Execute the shipped polling callbacks with slow promises and fake timers."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(autouse=True)
def clean_database():
    # These pure JavaScript tests never access the application's shared test DB.
    yield


HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
assert.match(source, /pollInFlight:\s*false/);
const matches = [...source.matchAll(/setInterval\((async \(\) => \{[\s\S]*?)\}, 5000\);/g)];
assert.equal(matches.length, 1, 'Exactly one five-second polling callback should be tested');
const update = {journeyVersion: 1, activityVersions: {sport: 1}};
const signature = JSON.stringify({version: 1, activities: [['sport', 1]]});
let calls = 0, draftCalls = 0;
const pending = [];
function slowRequest() {
  calls += 1;
  return new Promise((resolve, reject) => pending.push({resolve, reject}));
}
const state = {pollInFlight: false, journey: {id: 'event'}, section: 'dashboard',
               attendanceTab: 'recruits', session: {}, dirty: false, view: 'home',
               lastProtectionPoll: 0, lastUpdateSignature: signature};
const sandbox = {
  state, document: {hidden: false},
  api: (url) => {
    if (url.endsWith('/draft')) { draftCalls += 1; return Promise.resolve({}); }
    return slowRequest();
  },
  loadHome: slowRequest, renderSection: slowRequest,
  syncAdminRecruitAttendance: slowRequest, refreshProtectionPanel: slowRequest,
  loadRoster: slowRequest, toast: () => {},
};
const tick = vm.runInNewContext('(' + matches[0][1] + '})', sandbox);
async function settle(call, failure = false) {
  const current = pending.shift();
  if (failure) current.reject(new Error('Synthetic temporary network failure'));
  else current.resolve(update);
  // Attendance loadRoster handles errors itself in the application; allow our
  // deliberately rejecting stand-in while asserting the finally lock reset.
  await call.catch(() => {});
}
async function test() {
  sandbox.document.hidden = true;
  await tick();
  assert.equal(calls, 0, 'Hidden pages must not start requests');
  sandbox.document.hidden = false;
  const first = tick();
  assert.equal(calls, 1);
  assert.equal(state.pollInFlight, true);
  await Promise.all(Array.from({length: 6}, () => tick()));
  assert.equal(calls, 1, 'Slow requests must not accumulate polling requests');
  await sandbox.api('/api/evaluator/tasks/task/draft');
  assert.equal(draftCalls, 1, 'The polling lock must not serialize mutation requests');
  assert.equal(state.pollInFlight, true);
  await settle(first);
  assert.equal(state.pollInFlight, false);
  const second = tick();
  assert.equal(calls, 2, 'Polling resumes after a successful request');
  await settle(second, true);
  assert.equal(state.pollInFlight, false, 'Polling lock resets after request failure');
  const third = tick();
  assert.equal(calls, 3, 'A failed request must not permanently stop polling');
  await settle(third);
  assert.equal(state.pollInFlight, false);
  if (process.argv[1].endsWith('admin.js')) {
    for (const section of ['attendance', 'settings']) {
      state.section = section;
      const startCount = calls;
      const request = tick();
      assert.equal(calls, startCount + 1, section + ' polling should still execute');
      await tick();
      assert.equal(calls, startCount + 1, section + ' polling must not overlap');
      await settle(request);
    }
    state.dirty = true;
    const startCount = calls;
    await tick();
    assert.equal(calls, startCount, 'Unsaved admin changes still suppress background polling');
  }
  if (process.argv[1].endsWith('evaluator.js')) {
    state.lastUpdateSignature = '';
    const startCount = calls;
    const refresh = tick();
    pending.shift().resolve(update);
    // Drain cross-realm promise assimilation from the VM callback as well as
    // its first await before inspecting the chained home request.
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, startCount + 2, 'An update may chain a home refresh');
    await tick();
    assert.equal(calls, startCount + 2, 'The entire chained home refresh stays single-flight');
    await settle(refresh);
    assert.equal(state.pollInFlight, false);
  }
  if (process.argv[1].endsWith('recruit_attendance.js')) {
    state.session = null;
    const startCount = calls;
    await tick();
    assert.equal(calls, startCount, 'Logged-out attendance must not poll');
  }
  process.stdout.write('polling checks passed\n');
}
test().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize("filename", ["evaluator.js", "admin.js", "recruit_attendance.js"])
def test_background_polling_skips_overlapping_ticks_and_recovers(filename):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the frontend polling regression harness")
    asset = Path(__file__).parents[1] / "app" / "static" / filename
    result = subprocess.run([node, "-e", HARNESS, str(asset)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "polling checks passed" in result.stdout
