import { api, escapeHtml as h, fmt, localDateTime, selectedAccount, statusLabel, toast, wireAccountPicker } from "/static/common.js?v=20260810.1";
import { initializeSystemUI } from "/static/system-ui.js?v=20260820.3";

const systemConfiguration = await initializeSystemUI().catch(() => null);

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const host = $("#viewerHost");
const modal = $("#viewerModal");
const photoViewer = $("#photoViewer");
const dimensionOrder = systemConfiguration?.dimensions?.map(item => item.key) || ["willingness", "adaptability", "respect", "intelligence", "application", "physical_ability"];
const dimensionNames = Object.fromEntries((systemConfiguration?.dimensions || [
  { key: "willingness", name: "Willingness" }, { key: "adaptability", name: "Adaptability" },
  { key: "respect", name: "Respect" }, { key: "intelligence", name: "Intelligence" },
  { key: "application", name: "Application" }, { key: "physical_ability", name: "Physical Ability" },
]).map(item => [item.key, item.name]));
const dimensionMaximums = Object.fromEntries((systemConfiguration?.dimensions || []).map(item => [item.key, Number(item.displayMaximum || 5)]));
const dimensionGrade = (value, code) => Number(value || 0) * Number(dimensionMaximums[code] || 5);
// LRC compatibility expression retained by the generic display: dimensionGrade(item.score)
const COMPLETED_SCOPE = "completed";
const state = { session: null, accounts: [], journeys: [], journeyId: COMPLETED_SCOPE, data: null, tab: "results", attendanceTab: "recruits", resultsActivity: "overall", profileKey: "" };

function sectionHeading(eyebrow, title, description, actions = "") {
  return `<div class="section-heading"><div><p class="eyebrow">${h(eyebrow)}</p><h1>${h(title)}</h1>${description ? `<p class="muted">${h(description)}</p>` : ""}</div><div class="heading-actions">${actions}</div></div>`;
}

function openModal(content, wide = false) {
  $("#viewerModalBody").innerHTML = content;
  modal.classList.toggle("wide", wide);
  modal.showModal();
}
function closeModal() { modal.close(); modal.classList.remove("wide"); $("#viewerModalBody").innerHTML = ""; }
modal.onclick = event => { if (event.target === modal) closeModal(); };

function showPhoto(url, name) {
  $("#photoViewerImage").src = url;
  $("#photoViewerImage").alt = name;
  $("#photoViewerCaption").textContent = name;
  photoViewer.showModal();
}
document.addEventListener("click", event => {
  const trigger = event.target.closest("[data-view-photo]");
  if (trigger) showPhoto(trigger.dataset.viewPhoto, trigger.dataset.photoName || "Recruit photo");
});
$("#photoViewer .photo-viewer-close").onclick = () => photoViewer.close();
photoViewer.onclick = event => { if (event.target === photoViewer) photoViewer.close(); };

async function prepareLogin(message = "") {
  $("#viewerApp").classList.add("hidden");
  $("#viewerLoginView").classList.remove("hidden");
  $("#viewerLoginError").textContent = message;
  try {
    state.accounts = await api("/api/auth/usernames");
    wireAccountPicker($("#viewerUsername"), $("#viewerUsernames"), state.accounts);
  } catch { state.accounts = []; }
}

$("#viewerLoginForm").onsubmit = async event => {
  event.preventDefault();
  const values = new FormData(event.currentTarget);
  try {
    const account = selectedAccount(state.accounts, values.get("username"));
    if (!account) throw new Error("Select a username from the evaluator list.");
    const session = await api("/api/auth/login", { method: "POST", body: { username: account.username, password: values.get("password") } });
    if (!(session.isOwner || session.canAdmin || session.canResults)) throw new Error("This account does not have Results access.");
    state.session = session;
    await openApp();
  } catch (error) { $("#viewerLoginError").textContent = error.message; }
};

async function openApp() {
  $("#viewerLoginView").classList.add("hidden");
  $("#viewerApp").classList.add("hidden");
  $("#viewerName").textContent = state.session.username;
  state.journeys = await api("/api/view/journeys");
  if (state.journeyId !== COMPLETED_SCOPE && !state.journeys.some(item => item.id === state.journeyId)) state.journeyId = COMPLETED_SCOPE;
  $("#viewerJourney").innerHTML = `<option value="${COMPLETED_SCOPE}" ${state.journeyId === COMPLETED_SCOPE ? "selected" : ""}>All completed Journees</option>${state.journeys.map(item => `<option value="${item.id}" ${item.id === state.journeyId ? "selected" : ""}>${h(item.name)} · ${h(item.eventDate)}</option>`).join("")}`;
  await loadJourney();
  $("#viewerApp").classList.remove("hidden");
}

async function loadJourney() {
  state.data = await api(state.journeyId === COMPLETED_SCOPE ? "/api/view/completed" : `/api/view/journeys/${state.journeyId}`);
  if (!state.data.recruits.some(item => item.profileKey === state.profileKey)) state.profileKey = state.data.recruits[0]?.profileKey || "";
  await render();
}

$("#viewerJourney").onchange = async event => { state.journeyId = event.target.value; state.profileKey = ""; await loadJourney(); };
$("#viewerNav").onclick = async event => {
  const button = event.target.closest("button[data-tab]");
  if (!button) return;
  state.tab = button.dataset.tab;
  $("#viewerSidebar").classList.remove("open");
  await render();
};
$("#viewerMenu").onclick = () => $("#viewerSidebar").classList.toggle("open");
$("#viewerLogout").onclick = async () => {
  try { await api("/api/auth/logout", { method: "POST", headers: { "X-CSRF-Token": state.session.csrfToken } }); } catch {}
  state.session = null;
  await prepareLogin();
};

async function render() {
  $$("#viewerNav button").forEach(button => button.classList.toggle("active", button.dataset.tab === state.tab));
  if (state.tab === "attendance") renderAttendance();
  else if (state.tab === "results") renderResults();
  else await renderProfile();
}

function renderAttendance() {
  const recruits = [...state.data.recruits].sort((a, b) => a.name.localeCompare(b.name));
  const categoryRank = role => systemConfiguration?.assessorCategories?.find(item => item.key === role)?.primaryPriority ?? 999;
  const evaluatorGroup = item => (item.present ? 0 : 100) + categoryRank(item.role);
  const evaluators = [...state.data.evaluators].sort((a, b) => evaluatorGroup(a) - evaluatorGroup(b) || a.name.localeCompare(b.name));
  const recruitTable = `<div class="table-wrap"><table><thead><tr><th>Photo</th><th>Name</th><th>Journee</th><th>Phone number</th><th>Date of birth</th><th>Present</th><th>Arrival time</th><th>Attendance comment</th></tr></thead><tbody>${recruits.map(item => { const photo = `/api/view/journeys/${item.journeyId}/recruits/${item.id}/photo`; return `<tr class="${item.present ? "" : "inactive"}"><td>${item.hasPhoto ? `<button class="photo-zoom-trigger" data-view-photo="${photo}" data-photo-name="${h(item.name)}"><img class="avatar" src="${photo}" alt="${h(item.name)}"></button>` : `<span class="avatar placeholder">${h(item.name[0])}</span>`}</td><td><strong>${h(item.name)}</strong></td><td>${h(item.journeyName)}</td><td>${h(item.phoneNumber || "—")}</td><td>${h(item.dateOfBirth || "—")}</td><td><span class="status-pill ${item.present ? "completed" : "warning"}">${item.present ? "Present" : "Absent"}</span></td><td>${item.arrivalTime ? h(localDateTime(item.arrivalTime)) : "—"}</td><td>${h(item.attendanceComment || "—")}</td></tr>`; }).join("")}</tbody></table></div>`;
  const evaluatorTable = `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Journee</th><th>Present</th><th>Role</th><th>Mandatory room</th></tr></thead><tbody>${evaluators.map(item => `<tr class="${item.present ? "" : "inactive"}"><td><strong>${h(item.name)}</strong></td><td>${h(item.journeyName)}</td><td><span class="status-pill ${item.present ? "completed" : "warning"}">${item.present ? "Present" : "Absent"}</span></td><td><span class="role-badge ${item.role}">${h(item.role)}</span></td><td>${item.mandatoryRoom ? `Room ${item.mandatoryRoom}` : "—"}</td></tr>`).join("")}</tbody></table></div>`;
  host.innerHTML = `${sectionHeading("Confirmed roster", "Attendance", state.journeyId === COMPLETED_SCOPE ? "Attendance across every completed Journee." : "Confirmed attendance for this Journee.")}<div class="tabs"><button data-attendance="recruits" class="${state.attendanceTab === "recruits" ? "active" : ""}">Recruits</button><button data-attendance="evaluators" class="${state.attendanceTab === "evaluators" ? "active" : ""}">Evaluators</button></div><div class="panel">${state.attendanceTab === "recruits" ? recruitTable : evaluatorTable}</div>`;
  $$('[data-attendance]', host).forEach(button => button.onclick = () => { state.attendanceTab = button.dataset.attendance; renderAttendance(); });
}

function overallTable(rows) {
  return `<div class="table-wrap"><table><thead><tr><th>Rank</th><th>Recruit</th><th>Journee</th><th>Overall /${systemConfiguration?.scoreMaximum||20}</th>${dimensionOrder.map(code => `<th>${h(dimensionNames[code])} /${dimensionMaximums[code]||5}</th>`).join("")}<th>General</th><th>Color</th><th>Missing</th><th>General comment</th><th>Notes</th></tr></thead><tbody>${rows.map(row => `<tr><td><span class="rank-number">${row.overallRank ?? "—"}</span></td><td><button class="button ghost small result-profile" data-key="${row.profileKey}">${h(row.name)}</button></td><td>${h(row.journeyName)}</td><td><strong>${fmt(row.overallScore)}</strong></td>${dimensionOrder.map(code => `<td>${fmt(dimensionGrade(row.dimensions[code].score,code))}</td>`).join("")}<td>${fmt(row.generalAverage)}</td><td><span class="color-chip ${row.color}">${h(row.color)}</span></td><td>${row.missingCount}</td><td class="results-comment-cell">${h(row.generalComment || "—")}</td><td class="results-comment-cell">${h(row.notes || "—")}</td></tr>`).join("")}</tbody></table></div>`;
}
function dimensionTable(rows, code, average) {
  return `<p class="muted">Dimension average: <strong>${fmt(dimensionGrade(average,code))} /${dimensionMaximums[code]||5}</strong></p><div class="table-wrap"><table><thead><tr><th>Rank</th><th>Recruit</th><th>Journee</th><th>Grade /${dimensionMaximums[code]||5}</th><th>Coverage</th><th>Status</th></tr></thead><tbody>${rows.map(row => { const item = row.dimensions[code]; return `<tr><td><span class="rank-number">${item.rank ?? "—"}</span></td><td><button class="button ghost small result-profile" data-key="${row.profileKey}">${h(row.name)}</button></td><td>${h(row.journeyName)}</td><td><strong>${fmt(dimensionGrade(item.score,code))}</strong></td><td>${Math.round((item.availableWeight || 0) * 100)}%</td><td><span class="status-pill ${item.complete ? "completed" : "warning"}">${item.complete ? "Complete" : "Incomplete"}</span></td></tr>`; }).join("")}</tbody></table></div>`;
}
function activityTable(rows, code, average) {
  return `<p class="muted">Activity average: <strong>${fmt(average)} /5</strong></p><div class="table-wrap"><table><thead><tr><th>Rank</th><th>Recruit</th><th>Journee</th><th>Grade /5</th><th>Submissions</th><th>Status</th></tr></thead><tbody>${rows.map(row => { const item = row.activities[code]; return `<tr><td><span class="rank-number">${item.rank ?? "—"}</span></td><td><button class="button ghost small result-profile" data-key="${row.profileKey}">${h(row.name)}</button></td><td>${h(row.journeyName)}</td><td><strong>${fmt(item.score)}</strong></td><td>${item.submitted}/${item.expected}</td><td><span class="status-pill ${item.complete ? "completed" : "warning"}">${item.complete ? "Complete" : "Incomplete"}</span></td></tr>`; }).join("")}</tbody></table></div>`;
}
function renderResults() {
  const results = state.data.results;
  const tabs = [`<button data-result="overall" class="${state.resultsActivity === "overall" ? "active" : ""}">Overall /20</button>`, ...dimensionOrder.map(code => `<button data-result="dimension:${code}" class="${state.resultsActivity === `dimension:${code}` ? "active" : ""}">${h(results.dimensionNames?.[code] || dimensionNames[code])}</button>`), ...state.data.activities.map(item => `<button data-result="activity:${item.code}" class="${state.resultsActivity === `activity:${item.code}` ? "active" : ""}">${h(item.name)}</button>`)].join("");
  const rows = [...results.rows]; let table;
  if (state.resultsActivity === "overall") { rows.sort((a, b) => a.overallRank - b.overallRank); table = overallTable(rows); }
  else if (state.resultsActivity.startsWith("dimension:")) { const code = state.resultsActivity.split(":")[1]; rows.sort((a, b) => a.dimensions[code].rank - b.dimensions[code].rank); table = dimensionTable(rows, code, results.dimensionAverages[code]); }
  else { const code = state.resultsActivity.split(":")[1]; rows.sort((a, b) => a.activities[code].rank - b.activities[code].rank); table = activityTable(rows, code, results.activityAverages[code]); }
  host.innerHTML = `${sectionHeading("Scores", "Results & rankings", state.journeyId === COMPLETED_SCOPE ? "Shared rankings across every completed Journee." : "Rankings for this Journee.")}<div class="panel"><div class="tabs">${tabs}</div>${table}</div>`;
  $$('[data-result]', host).forEach(button => button.onclick = () => { state.resultsActivity = button.dataset.result; renderResults(); });
  $$(".result-profile", host).forEach(button => button.onclick = async () => { state.profileKey = button.dataset.key; state.tab = "profiles"; await render(); });
}

function radar(result, items, maximum, viewBox = "0 0 360 320") {
  const cx = 180, cy = 155, radius = 104;
  const point = (index, value) => { const angle = -Math.PI / 2 + index * Math.PI * 2 / items.length; return [cx + Math.cos(angle) * radius * value, cy + Math.sin(angle) * radius * value]; };
  const polygon = value => items.map((_, index) => point(index, value).join(",")).join(" ");
  const data = items.map((item, index) => point(index, Math.max(0, Math.min(maximum, Number(item.score || 0))) / maximum));
  return `<svg class="activity-radar" viewBox="${viewBox}">${[.2,.4,.6,.8,1].map(level => `<polygon class="radar-grid" points="${polygon(level)}"></polygon>`).join("")}${items.map((_, index) => { const p = point(index, 1); return `<line class="radar-axis" x1="${cx}" y1="${cy}" x2="${p[0]}" y2="${p[1]}"></line>`; }).join("")}<polygon class="radar-data" points="${data.map(p => p.join(",")).join(" ")}"></polygon>${data.map(p => `<circle class="radar-dot" cx="${p[0]}" cy="${p[1]}" r="4"></circle>`).join("")}${items.map((item, index) => { const p = point(index, 1.24); return `<text class="radar-label" x="${p[0]}" y="${p[1]}" text-anchor="middle"><tspan x="${p[0]}">${h(item.name)}</tspan><tspan x="${p[0]}" dy="15">${fmt(item.score)}/${maximum}</tspan></text>`; }).join("")}</svg>`;
}

function auditItem(item) {
  return `<article class="audit-item"><div><strong>${h(statusLabel(item.action))}</strong><p>${h(item.actorName || "System")}${item.reason ? ` · ${h(item.reason)}` : ""}</p></div><time>${h(localDateTime(item.createdAt))}</time></article>`;
}

function profileHtml(profile) {
  const result = profile.result;
  const activities = state.data.activities;
  const dimensionItems = dimensionOrder.map(code => ({ name: dimensionNames[code], score: dimensionGrade(result.dimensions?.[code]?.score,code) }));
  const activityItems = activities.map(activity => ({ name: activity.name, score: result.activities?.[activity.code]?.score || 0 }));
  const photo = profile.photoUrl ? `<button class="photo-zoom-trigger profile-photo-trigger" data-view-photo="${profile.photoUrl}" data-photo-name="${h(profile.recruit.name)}"><img class="profile-photo" src="${profile.photoUrl}" alt="${h(profile.recruit.name)}"></button>` : `<span class="profile-photo avatar placeholder">${h(profile.recruit.name[0])}</span>`;
  const missing = result.missingComponents || [];
  return `<div class="panel"><div class="profile-header">${photo}<div><h2>${h(profile.recruit.name)}</h2><p class="eyebrow">${h(profile.journey?.name || "")}</p><p class="muted">${h(profile.recruit.phoneNumber || "No phone number")} · ${profile.recruit.dateOfBirth ? `Date of birth: ${h(profile.recruit.dateOfBirth)}` : "Date of birth not recorded"} · ${profile.recruit.present ? "Present" : "Absent"}</p><p class="profile-arrival"><strong>Arrival time:</strong> ${profile.recruit.arrivalTime ? h(localDateTime(profile.recruit.arrivalTime)) : "Not recorded"}${profile.recruit.attendanceComment ? `<small class="profile-attendance-note">${h(profile.recruit.attendanceComment)}</small>` : ""}</p></div><div class="profile-score-group"><div class="grade-orb ${result.color}"><strong>${h(result.color)}</strong><small>Color grade</small></div><div class="score-orb"><div><strong>${fmt(result.overallScore)}</strong><small>/20 · rank ${result.overallRank ?? "—"}</small></div></div></div></div></div>
    <div class="panel"><div class="panel-header"><div><h2>Dimension performance</h2><p class="muted">Select a dimension to inspect every criterion and evaluator grade.</p></div></div><div class="profile-performance"><div class="radar-wrap">${radar(result, dimensionItems, dimensionMaximums[dimensionOrder[0]]||5)}</div><div class="profile-dimension-grid">${dimensionOrder.map(code => { const item = result.dimensions[code]; return `<button class="profile-activity dimension-card" data-dimension="${code}"><small>${h(dimensionNames[code])}</small><strong>${fmt(dimensionGrade(item.score,code))} /${dimensionMaximums[code]||5}</strong><small>Rank ${item.rank ?? "—"} · ${item.complete ? "Complete" : "Incomplete"}</small><span class="dimension-card-action">View criteria →</span></button>`; }).join("")}</div></div></div>
    <div class="panel"><h2>Activity performance</h2><p class="muted">Select an activity to inspect its evaluator submissions.</p><div class="profile-performance"><div class="radar-wrap">${radar(result, activityItems, 5)}</div><div class="profile-activity-grid">${activities.map(activity => { const item = result.activities[activity.code]; return `<button class="profile-activity activity-card-button" data-activity="${activity.code}"><small>${h(activity.name)}</small><strong>${fmt(item.score)} /5</strong><small>Rank ${item.rank ?? "—"} · ${item.submitted}/${item.expected}</small><span class="dimension-card-action">View evaluations →</span></button>`; }).join("")}</div></div></div>
    <div class="two-column"><div class="panel"><h2>General assessment</h2><form id="viewerGeneralAssessmentForm" class="stack"><div class="three-column"><label>Punctuality<input name="punctuality" type="number" min="0" max="1" step="0.1" value="${profile.assessment.punctuality ?? ""}"></label><label>Respect to us<input name="respect" type="number" min="0" max="1" step="0.1" value="${profile.assessment.respect ?? ""}"></label><label>Seriousness<input name="seriousness" type="number" min="0" max="1" step="0.1" value="${profile.assessment.seriousness ?? ""}"></label></div><label>General comment<textarea name="comment">${h(profile.assessment.comment)}</textarea></label><label>Notes<textarea name="notes" rows="5">${h(profile.assessment.notes)}</textarea></label><div class="inline-actions"><button type="button" class="button ghost" id="viewerDiscardAssessment">Discard</button><button class="button primary" id="viewerSaveAssessment">Save general assessment</button></div></form></div><div class="panel"><h2>Completion</h2><p><strong>${result.missingCount}</strong> missing component${result.missingCount === 1 ? "" : "s"}</p>${missing.length ? `<div class="member-list">${missing.map(item => `<span class="member-chip">${h(item)}</span>`).join("")}</div>` : `<p class="success-text">All activities and general grades are complete.</p>`}</div></div>
    <div class="panel"><h2>Evaluator breakdown</h2>${Object.entries(profile.evaluations).map(([code, entries]) => `<h3 style="margin-top:16px">${h(state.data.activities.find(item => item.code === code)?.name || statusLabel(code))}</h3>${entries.length ? `<div class="table-wrap"><table><thead><tr><th>Evaluator</th><th>Role</th><th>Score</th><th>Status</th><th>Comment</th><th>Evaluation</th></tr></thead><tbody>${entries.map((entry, index) => `<tr><td>${h(entry.evaluatorName)}</td><td>${h(entry.evaluatorRole)}</td><td>${entry.submission ? fmt(entry.submission.score) : "—"}</td><td>${entry.submission ? h(statusLabel(entry.submission.status)) : "Missing"}</td><td>${h(entry.submission?.comments || "")}</td><td>${entry.submission ? `<button class="button secondary small view-evaluation" data-code="${code}" data-index="${index}">View evaluation</button>` : "—"}</td></tr>`).join("")}</tbody></table></div>` : `<p class="subtle">No published evaluator assignment.</p>`}`).join("")}</div>
    <div class="panel"><h2>Profile audit history</h2>${profile.history.length ? `<div class="audit-list">${profile.history.map(auditItem).join("")}</div>` : `<p class="muted">No profile changes yet.</p>`}</div>`;
}

async function renderProfile() {
  const recruits = [...state.data.recruits].sort((a, b) => a.name.localeCompare(b.name) || a.journeyName.localeCompare(b.journeyName));
  const selected = recruits.find(item => item.profileKey === state.profileKey);
  const scopeQuery = state.journeyId === COMPLETED_SCOPE ? "?scope=completed" : "";
  const profile = selected ? await api(`/api/view/journeys/${selected.journeyId}/recruits/${selected.id}/profile${scopeQuery}`) : null;
  host.innerHTML = `${sectionHeading("Individual record", "Recruit profile", "Grades, rankings, comments, and evaluation history.", `<select id="viewerRecruit">${recruits.map(item => `<option value="${item.profileKey}" ${item.profileKey === state.profileKey ? "selected" : ""}>${h(item.name)} · ${h(item.journeyName)}</option>`).join("")}</select>`)}${profile ? profileHtml(profile) : `<div class="empty-state"><h2>No recruits</h2></div>`}`;
  $("#viewerRecruit")?.addEventListener("change", async event => { state.profileKey = event.target.value; await renderProfile(); });
  if (!profile) return;
  $$(".dimension-card", host).forEach(button => button.onclick = () => showDimension(profile, button.dataset.dimension));
  $$(".activity-card-button", host).forEach(button => button.onclick = () => showActivity(profile, button.dataset.activity));
  $$(".view-evaluation", host).forEach(button => button.onclick = () => showEvaluation(profile, button.dataset.code, Number(button.dataset.index)));
  const assessmentForm = $("#viewerGeneralAssessmentForm");
  if (assessmentForm) {
    const factors = new Map((systemConfiguration?.generalFactors || []).map(item => [item.storageKey, item]));
    for (const key of ["punctuality", "respect", "seriousness"]) {
      const input = assessmentForm.elements[key];
      const factor = factors.get(key);
      input.closest("label").classList.toggle("hidden", !factor || systemConfiguration?.features?.generalAssessment === false);
      if (factor) { input.max = factor.maximum; input.step = factor.step; }
    }
    assessmentForm.elements.comment.closest("label").classList.toggle("hidden", systemConfiguration?.features?.generalAssessment === false);
    assessmentForm.elements.notes.closest("label").classList.toggle("hidden", systemConfiguration?.features?.notes === false);
  }
  $("#viewerDiscardAssessment")?.addEventListener("click", () => renderProfile());
  $("#viewerGeneralAssessmentForm")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const optionalGrade = name => form.get(name) === "" ? null : Number(form.get(name));
    const saveButton = $("#viewerSaveAssessment");
    saveButton.disabled = true;
    try {
      await api(`/api/view/journeys/${profile.journey.id}/recruits/${profile.recruit.id}/profile`, {
        method: "PUT",
        headers: { "X-CSRF-Token": state.session.csrfToken },
        body: {
          punctuality: optionalGrade("punctuality"),
          respect: optionalGrade("respect"),
          seriousness: optionalGrade("seriousness"),
          comment: form.get("comment") || "",
          notes: form.get("notes") || "",
          base_version: profile.assessment.version,
        },
      });
      toast("General assessment saved.");
      await loadJourney();
    } catch (error) {
      toast(error.message, "error");
      saveButton.disabled = false;
    }
  });
}

function showDimension(profile, code) {
  const breakdown = profile.dimensionBreakdowns?.[code];
  const sections = breakdown.activities.map(activity => `<section class="dimension-activity-section"><div class="panel-header"><div><h3>${h(activity.name)}</h3><p class="muted">Activity grade: ${fmt(activity.activityScore)} /5</p></div></div><div class="dimension-criteria-list">${activity.criteria.map(criterion => `<article class="dimension-criterion"><div class="dimension-criterion-heading"><div><strong>${h(criterion.name)}</strong><p class="muted">${h(criterion.explanation)}</p></div><div class="criterion-math"><span>Weight ${fmt(criterion.weight * 100)}%</span><strong>${criterion.criterionAverage == null ? "—" : `${fmt(criterion.criterionAverage)} /5`}</strong><small>Contribution ${fmt(dimensionGrade(criterion.weightedContribution,code))} /${dimensionMaximums[code]||5}</small></div></div><div class="criterion-evaluators">${criterion.evaluators.map(evaluator => `<div class="criterion-evaluator ${evaluator.grade == null ? "missing" : ""}"><div><strong>${h(evaluator.evaluatorName)}</strong><span class="role-badge ${h(evaluator.evaluatorRole)}">${h(evaluator.evaluatorRole)}</span></div><div class="criterion-grade"><strong>${evaluator.grade == null ? "Missing" : `${fmt(evaluator.grade)} /5`}</strong>${evaluator.rawValue != null ? `<small>Result: ${h(evaluator.rawValue)}${criterion.unit ? ` ${h(criterion.unit)}` : ""}</small>` : ""}<small>${h(statusLabel(evaluator.status))}</small></div></div>`).join("") || `<p class="subtle">No evaluator assignment.</p>`}</div></article>`).join("")}</div></section>`).join("");
  openModal(`<div><p class="eyebrow">Dimension grading</p><div class="panel-header"><div><h2>${h(profile.recruit.name)} · ${h(breakdown.name)}</h2><p class="muted">Every evaluator grade used for this dimension.</p></div><div class="dimension-modal-score"><strong>${fmt(dimensionGrade(breakdown.score,code))} /${dimensionMaximums[code]||5}</strong><small>Rank ${breakdown.rank ?? "—"} · ${breakdown.complete ? "Complete" : "Incomplete"}</small></div></div><div class="dimension-breakdown-scroll">${sections}</div><div class="modal-actions"><button class="button primary" id="closeViewerModal">Close</button></div></div>`, true);
  $("#closeViewerModal").onclick = closeModal;
}

function showActivity(profile, code) {
  const activity = state.data.activities.find(item => item.code === code);
  const entries = profile.evaluations[code] || [];
  const result = profile.result.activities[code];
  openModal(`<div><p class="eyebrow">Activity grading</p><div class="panel-header"><div><h2>${h(profile.recruit.name)} · ${h(activity?.name || statusLabel(code))}</h2><p class="muted">Every evaluator submission contributing to this activity grade.</p></div><div class="dimension-modal-score"><strong>${fmt(result.score)} /5</strong><small>Rank ${result.rank ?? "—"} · ${result.submitted}/${result.expected} submitted</small></div></div><div class="dimension-breakdown-scroll">${entries.length ? entries.map((entry, index) => `<article class="dimension-criterion"><div class="panel-header"><div><strong>${h(entry.evaluatorName)}</strong> <span class="role-badge ${h(entry.evaluatorRole)}">${h(entry.evaluatorRole)}</span><p class="muted">${entry.submission ? h(entry.submission.comments || "No comment") : "Evaluation not submitted"}</p></div><div class="criterion-evaluator-actions"><div class="criterion-grade"><strong>${entry.submission ? `${fmt(entry.submission.score)} /5` : "Missing"}</strong><small>${entry.submission ? h(statusLabel(entry.submission.status)) : "No submission"}</small></div>${entry.submission ? `<button class="button secondary small modal-view-evaluation" data-index="${index}">View evaluation</button>` : ""}</div></div></article>`).join("") : `<div class="empty-state"><p>No evaluator assignments.</p></div>`}</div><div class="modal-actions"><button class="button primary" id="closeViewerModal">Close</button></div></div>`, true);
  $("#closeViewerModal").onclick = closeModal;
  $$(".modal-view-evaluation", modal).forEach(button => button.onclick = () => { closeModal(); showEvaluation(profile, code, Number(button.dataset.index)); });
}

async function showEvaluation(profile, code, index) {
  const entry = profile.evaluations[code][index];
  let detail = entry.submission;
  if (detail?.id) detail = await api(`/api/view/journeys/${profile.journey.id}/submissions/${detail.id}`);
  const rubric = detail.rubric || profile.rubrics[code];
  const values = rubric.criteria.map(criterion => { const value = rubric.kind === "sport" ? detail.raw?.[criterion.key] : detail.responses?.[criterion.key]; return `<tr><td><strong>${h(criterion.name)}</strong><small class="muted">${h(criterion.explanation)}</small></td><td>${value == null || value === "" ? "—" : `${h(value)}${rubric.kind === "sport" && criterion.unit ? ` ${h(criterion.unit)}` : " /5"}`}</td></tr>`; }).join("");
  openModal(`<div><p class="eyebrow">Evaluation detail</p><h2>${h(entry.evaluatorName)} · ${h(rubric.name)}</h2><div class="panel-header"><p>${h(profile.recruit.name)}</p><div class="dimension-modal-score"><strong>${fmt(detail.score)} /5</strong><small>${h(statusLabel(detail.status))}</small></div></div><div class="table-wrap"><table><thead><tr><th>Criterion</th><th>Entered value</th></tr></thead><tbody>${values}</tbody></table></div>${detail.comments ? `<div class="panel"><h3>Comment</h3><p>${h(detail.comments)}</p></div>` : ""}${detail.history?.length ? `<div class="panel"><h3>Edit history</h3><div class="audit-list">${detail.history.map(item => `<article class="audit-item"><div><strong>Version ${item.version}</strong><p>${h(item.actorName || item.actorType)}${item.reason ? ` · ${h(item.reason)}` : ""}</p></div><time>${h(localDateTime(item.createdAt))}</time></article>`).join("")}</div></div>` : ""}<div class="modal-actions"><button class="button primary" id="closeViewerModal">Close</button></div></div>`, true);
  $("#closeViewerModal").onclick = closeModal;
}

(async () => {
  try {
    state.session = await api("/api/auth/session");
    if (!(state.session.isOwner || state.session.canAdmin || state.session.canResults)) throw new Error();
    await openApp();
  } catch { await prepareLogin(); }
})();
