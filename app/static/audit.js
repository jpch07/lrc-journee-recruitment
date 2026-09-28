import { escapeHtml as h, localDateTime } from './common.js?v=20260810.1';

const label = value => String(value || '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_.]/g, ' ').replace(/^./, c => c.toUpperCase());
const value = input => {
  if (input == null || input === '') return 'Not set';
  if (typeof input === 'boolean') return input ? 'Yes' : 'No';
  if (typeof input === 'object') return 'Updated';
  if (typeof input === 'number' || /^-?\d+(\.\d+)?$/.test(String(input))) return new Intl.NumberFormat(undefined, {maximumFractionDigits: 2}).format(Number(input));
  return String(input);
};
const changeRow = change => `<div class="audit-change"><span>${h(change.label)}${change.activity ? `<small>${h(change.activity)}</small>` : ''}</span><span class="audit-change-values"><span class="muted">${h(value(change.before))}</span><span aria-label="changed to"> → </span><strong>${h(value(change.after))}</strong>${change.maximum != null ? `<small> /${h(value(change.maximum))}</small>` : ''}</span></div>`;

export function auditItem(item) {
  const changes = item.changes || ['username', 'fullName', 'active', 'canAdmin', 'canResults', 'canEvaluate', 'role'].filter(key => item.before?.[key] !== item.after?.[key]).map(key => ({label: label(key), before: item.before?.[key], after: item.after?.[key]}));
  const criteria = item.criteriaChanges || [];
  return `<article class="audit-entry"><div class="audit-entry-heading"><div>${item.entityName ? `<strong class="audit-person">${h(item.entityName)}</strong>` : ''}<h3>${h(item.title || label(item.action))}</h3></div><time datetime="${h(item.createdAt || '')}">${h(localDateTime(item.createdAt))}</time></div>
    <p class="audit-meta">${h(item.actorName || 'System')}${item.journeyName ? ` · ${h(item.journeyName)}` : ''}</p>
    ${changes.length ? `<div class="audit-change-list">${changes.map(changeRow).join('')}</div>` : ''}
    ${item.reason ? `<p class="audit-reason">${h(item.reason)}</p>` : ''}
    ${criteria.length ? `<details class="audit-details"><summary>${criteria.length} criterion ${criteria.length === 1 ? 'change' : 'changes'}</summary><div class="audit-change-list">${criteria.map(changeRow).join('')}</div></details>` : ''}
  </article>`;
}

export async function mountWorkspaceAudit(host, {api, journeys, back, journeyId = ''}) {
  host.innerHTML = `<div class="section-heading"><div><h1>Workspace audit</h1><p class="muted">Changes across all Journees, including archived events.</p></div><button class="button ghost" id="auditBack">Back to library</button></div>
    <form id="auditFilters" class="audit-filters"><label>Person<input type="search" name="search" placeholder="Recruit, evaluator or account name" maxlength="200"></label><label>Journee<select name="journey_id"><option value="">All Journees</option>${journeys.map(j => `<option value="${h(j.id)}" ${j.id === journeyId ? 'selected' : ''}>${h(j.name)}${j.archived ? ' (archived)' : ''}</option>`).join('')}</select></label><label>Action<select name="action"><option value="">All actions</option><option value="management.">Grade corrections</option><option value="evaluation.">Evaluations</option><option value="recruit.">Recruit profiles</option><option value="attendance.">Attendance</option><option value="assignment">Assignments</option><option value="room">Rooms</option><option value="journey.">Journees</option></select></label><label>From (UTC)<input type="date" name="start"></label><label>Through (UTC)<input type="date" name="end"></label><div class="audit-filter-actions"><button class="button primary" type="submit">Apply filters</button><button class="button ghost" type="reset">Clear</button></div></form>
    <p id="auditStatus" class="muted" role="status"></p><div id="auditFeed" class="audit-feed"></div><button id="auditMore" class="button ghost hidden">Load older events</button>`;
  host.querySelector('#auditBack').onclick = back;
  const form = host.querySelector('#auditFilters'), feed = host.querySelector('#auditFeed'), status = host.querySelector('#auditStatus'), more = host.querySelector('#auditMore');
  let cursor = null, generation = 0, count = 0;
  async function load(append = false) {
    const request = ++generation;
    if (!append) { cursor = null; count = 0; feed.innerHTML = ''; }
    more.disabled = true;
    status.textContent = 'Loading history…';
    const params = new URLSearchParams();
    for (const [key, val] of new FormData(form)) if (val) params.set(key, val);
    if (append && cursor) params.set('cursor', cursor);
    try {
      const data = await api(`/api/admin/audit?${params}`);
      if (request !== generation || !host.contains(feed)) return;
      feed.insertAdjacentHTML('beforeend', data.items.map(auditItem).join(''));
      count += data.items.length; cursor = data.nextCursor;
      status.textContent = count ? `${count} ${count === 1 ? 'event' : 'events'} shown${cursor ? ' · More history available' : ' · End of history'}` : 'No events match these filters.';
      more.classList.toggle('hidden', !cursor);
    } catch (error) {
      if (request === generation) status.textContent = `${error.message} Use Apply filters to retry.`;
    } finally { if (request === generation) more.disabled = false; }
  }
  form.onsubmit = event => { event.preventDefault(); load(); };
  form.onreset = () => { setTimeout(() => { form.elements.journey_id.value = ''; load(); }, 0); };
  more.onclick = () => load(true);
  await load();
}
