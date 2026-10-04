// A manual action, not a timer. No spreadsheet credentials or data enter the browser.
const sheetUrl = 'https://docs.google.com/spreadsheets/d/11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0/edit';

export function mountSheetBackup(host, {api, mutation}) {
  if (host.dataset.mounted) return;
  host.dataset.mounted = 'true';
  host.innerHTML = `<section class="sheet-backup" aria-labelledby="sheetBackupTitle">
    <div class="sheet-backup-copy">
      <h2 id="sheetBackupTitle">Workspace backup</h2>
      <p class="muted">All Journees, evaluations, accounts and photos in one Google spreadsheet. Updates only when you press Back up.</p>
      <p data-backup-status role="status" aria-live="polite">Checking connection…</p>
      <p data-backup-last class="muted"></p>
      <p class="sheet-backup-privacy muted">Contains personal information. Anyone who can access the spreadsheet can read the backup. Passwords and login tokens are excluded.</p>
    </div>
    <div class="sheet-backup-actions">
      <button type="button" class="button secondary" data-backup-start disabled>Back up workspace</button>
      <button type="button" class="button ghost" data-backup-check>Check connection</button>
      <button type="button" class="button ghost" data-backup-cancel hidden>Cancel backup</button>
      <a class="button ghost" href="${sheetUrl}" target="_blank" rel="noopener noreferrer">Open spreadsheet</a>
    </div>
  </section>`;
  const status = host.querySelector('[data-backup-status]');
  const last = host.querySelector('[data-backup-last]');
  const start = host.querySelector('[data-backup-start]');
  const check = host.querySelector('[data-backup-check]');
  const cancel = host.querySelector('[data-backup-cancel]');
  let job = null;
  let running = false;
  let cancelRequested = false;
  let connected = false;
  const leaving = event => {
    if (!running) return;
    event.preventDefault(); event.returnValue = '';
  };
  window.addEventListener('beforeunload', leaving);

  function error(message) {
    status.textContent = message;
    status.classList.add('form-error');
  }
  function setControls() {
    start.disabled = running || !connected;
    check.disabled = running;
    cancel.hidden = !job || job.state === 'complete' || job.state === 'cancelled';
    cancel.disabled = cancelRequested;
    start.textContent = running ? 'Backing up…' : job && !['complete','cancelled'].includes(job.state) ? 'Retry upload' : 'Back up workspace';
  }
  async function refresh() {
    check.disabled = true;
    try {
      const info = await api('/api/admin/sheet-backup');
      connected = info.connected;
      job = info.job || null;
      status.classList.remove('form-error');
      status.textContent = !connected ? info.message || 'One-time Google connection is not set up yet.' :
        job ? 'An interrupted backup is available. Press Retry upload to continue.' :
        info.state === 'busy' ? 'Another backup is running. Check again after it finishes.' : 'Ready for a manual backup.';
      if (info.state === 'busy' && !job) connected = false;
      last.textContent = info.lastComplete?.snapshotAt ?
        `Last complete snapshot: ${new Date(info.lastComplete.snapshotAt).toLocaleString()}` : 'No completed backup verified yet.';
    } catch (err) {
      connected = false;
      error(`Could not check the backup connection. ${err.message}`);
    } finally { setControls(); }
  }
  async function run() {
    if (running) return;
    running = true;
    cancelRequested = false;
    status.classList.remove('form-error');
    status.textContent = 'Preparing a consistent workspace snapshot. Keep this tab open…';
    setControls();
    try {
      if (!job || ['complete','cancelled'].includes(job.state)) job = await api('/api/admin/sheet-backup/start', mutation());
      setControls();
      while (job.state !== 'complete') {
        if (cancelRequested) {
          job = await api(`/api/admin/sheet-backup/${encodeURIComponent(job.jobId)}/cancel`, mutation());
          break;
        }
        job = await api(`/api/admin/sheet-backup/${encodeURIComponent(job.jobId)}/advance`, mutation());
        status.textContent = job.state === 'complete' ? job.message : `${job.message} ${job.progress} / ${job.total} steps`;
      }
      status.textContent = job.message;
      if (job.state === 'complete') {
        const info = await api('/api/admin/sheet-backup');
        if (info.lastComplete?.snapshotAt) last.textContent = `Last complete snapshot: ${new Date(info.lastComplete.snapshotAt).toLocaleString()}`;
      }
    } catch (err) {
      error(`${err.message} ${job ? 'Use Retry upload, or cancel this run and start again.' : 'Check the connection before trying again.'}`);
    } finally {
      running = false;
      cancelRequested = false;
      setControls();
    }
  }
  start.addEventListener('click', run);
  check.addEventListener('click', refresh);
  cancel.addEventListener('click', async () => {
    cancelRequested = true;
    setControls();
    if (running) { status.textContent = 'Cancelling after the current safe upload step…'; return; }
    try {
      job = await api(`/api/admin/sheet-backup/${encodeURIComponent(job.jobId)}/cancel`, mutation());
      status.textContent = job.message;
      status.classList.remove('form-error');
    } catch (err) { error(err.message); }
    finally { cancelRequested = false; setControls(); }
  });
  void refresh();
}
