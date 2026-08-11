import { api, durationPickerHtml, escapeHtml as h, fmt, localDateTime, selectedAccount, statusLabel, toast, uid, wireAccountPicker, wireBoundedNumberInputs, wireDurationPickers, wireRecruitDirectoryPicker } from "/static/common.js?v=20260810.1";

const state = {
  csrf: "",
  adminName: "",
  isOwner: false,
  testToolsEnabled: false,
  simulatorActivated: false,
  journeys: [],
  journey: null,
  section: "dashboard",
  attendanceTab: "recruits",
  attendanceDraft: null,
  assignmentActivity: "sport",
  monitoringActivity: "sport",
  monitoringMode: "recruits",
  resultsActivity: "overall",
  roomPlan: null,
  assignmentRound: null,
  profileId: null,
  dirty: false,
  pollTimer: null,
  lastProtectionPoll: 0,
  recruitAttendanceSaves: new Map(),
  loginAccounts: [],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const host = $("#sectionHost");
const modal = $("#modal");
const photoViewer = $("#photoViewer");

const dimensionOrder = ["willingness", "adaptability", "respect", "intelligence", "application", "physical_ability"];
const dimensionNames = {
  willingness: "Willingness",
  respect: "Respect",
  adaptability: "Adaptability",
  intelligence: "Intelligence",
  application: "Application",
  physical_ability: "Physical Ability",
};

function showPhotoViewer(url, name) {
  $("#photoViewerImage").src = url;
  $("#photoViewerImage").alt = name;
  $("#photoViewerCaption").textContent = name;
  photoViewer.showModal();
}

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-photo-viewer]");
  if (!trigger) return;
  event.preventDefault();
  event.stopPropagation();
  showPhotoViewer(trigger.dataset.photoUrl, trigger.dataset.photoName || "Recruit photo");
});
$(".photo-viewer-close").onclick = () => photoViewer.close();
photoViewer.onclick = (event) => { if (event.target === photoViewer) photoViewer.close(); };

function mutation(method = "POST", body, extraHeaders = {}) {
  return { method, body, headers: { "X-CSRF-Token": state.csrf, ...extraHeaders } };
}

function showLogin() {
  $("#loginView").classList.remove("hidden");
  $("#appView").classList.add("hidden");
}

function showApp() {
  $("#loginView").classList.add("hidden");
  $("#appView").classList.remove("hidden");
  $("#adminName").textContent = state.adminName;
}

function setDirty(value) {
  state.dirty = value;
}

function guardDirty() {
  if (state.recruitAttendanceSaves.size) {
    toast("Wait for the current recruit changes to finish saving.", "error");
    return false;
  }
  return !state.dirty || confirm("You have unsaved changes. Discard them and continue?");
}

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty && !state.recruitAttendanceSaves.size) return;
  event.preventDefault();
  event.returnValue = "";
});

function openModal(content, { wide = false } = {}) {
  $("#modalBody").innerHTML = content;
  modal.classList.toggle("wide", wide);
  modal.showModal();
}

function closeModal() {
  modal.close();
  modal.classList.remove("wide");
  $("#modalBody").innerHTML = "";
}

modal.addEventListener("click", (event) => {
  if (event.target === modal) closeModal();
});

async function initialize() {
  await loadAccountUsernames();
  try {
    const session = await api("/api/auth/session");
    if (!session.isOwner && !session.canAdmin) throw new Error("Administration access is not permitted.");
    state.csrf = session.csrfToken;
    state.adminName = session.username;
    state.isOwner = Boolean(session.isOwner);
    state.testToolsEnabled = Boolean(session.testToolsEnabled);
    showApp();
    await loadLibrary();
  } catch {
    showLogin();
  }
}

async function loadAccountUsernames() {
  try {
    state.loginAccounts = await api("/api/auth/usernames");
    wireAccountPicker($("#adminUsername"), $("#accountUsernames"), state.loginAccounts);
  } catch { state.loginAccounts = []; }
}

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  $("#loginError").textContent = "";
  try {
    const account = selectedAccount(state.loginAccounts, form.get("username"));
    if (!account) throw new Error("Select a username from the evaluator list.");
    const result = await api("/api/auth/login", {
      method: "POST",
      body: { username: account.username, password: form.get("password") },
    });
    if (!result.isOwner && !result.canAdmin) throw new Error("This account does not have administration access.");
    state.csrf = result.csrfToken;
    state.adminName = result.username;
    state.isOwner = Boolean(result.isOwner);
    state.testToolsEnabled = Boolean(result.testToolsEnabled);
    showApp();
    await loadLibrary();
  } catch (error) {
    $("#loginError").textContent = error.message;
  }
});

$("#logoutButton").addEventListener("click", async () => {
  if (!guardDirty()) return;
  await api("/api/auth/logout", mutation());
  state.journey = null;
  showLogin();
});

$("#libraryButton").addEventListener("click", returnToLibrary);
$("#backToLibrary").addEventListener("click", returnToLibrary);
$("#menuButton").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
$("#showArchived").addEventListener("change", loadLibrary);
$("#createJourneyButton").addEventListener("click", createJourneyDialog);

async function loadLibrary() {
  clearInterval(state.pollTimer);
  const include = $("#showArchived").checked;
  state.journeys = await api(`/api/admin/journeys?include_archived=${include}`);
  $("#libraryView").classList.remove("hidden");
  $("#workspaceView").classList.add("hidden");
  $("#journeyCrumb").textContent = "Journee library";
  $("#permissionsNav").classList.toggle("hidden", !state.isOwner);
  renderLibrary();
}

function renderLibrary() {
  const grid = $("#journeyGrid");
  if (!state.journeys.length) {
    grid.innerHTML = `<div class="empty-state"><h2>No Journees yet</h2><p class="muted">Create your first event workspace to begin.</p><button class="button primary" id="emptyCreate">Create Journee</button></div>`;
    $("#emptyCreate").onclick = createJourneyDialog;
    return;
  }
  grid.innerHTML = state.journeys.map((journey) => `
    <article class="journey-card" data-id="${journey.id}">
      <div>
        <span class="status-pill ${journey.status}">${h(statusLabel(journey.status))}</span>
        <h2 style="margin-top:12px">${h(journey.name)}</h2>
        <div class="meta-row"><span>${h(journey.eventDate)}</span><span>${journey.roomCount} room${journey.roomCount === 1 ? "" : "s"}</span></div>
      </div>
      <div class="counts"><div><strong>${journey.presentRecruitCount}/${journey.recruitCount}</strong><small>Recruits present</small></div><div><strong>${journey.presentEvaluatorCount}/${journey.evaluatorCount}</strong><small>Evaluators present</small></div></div>
      <div class="inline-actions"><button class="button primary open-journey">Open</button><button class="button ghost small duplicate-journey">Duplicate</button>${journey.status !== "archived" ? `<button class="button ghost small archive-journey">Archive</button>` : ""}<button class="button danger small delete-journey">Delete</button></div>
    </article>`).join("");
  $$(".open-journey", grid).forEach((button) => button.onclick = () => openJourney(button.closest("article").dataset.id));
  $$(".duplicate-journey", grid).forEach((button) => button.onclick = () => duplicateJourney(button.closest("article").dataset.id));
  $$(".archive-journey", grid).forEach((button) => button.onclick = () => archiveJourney(button.closest("article").dataset.id));
  $$(".delete-journey", grid).forEach((button) => button.onclick = () => deleteJourney(button.closest("article").dataset.id));
}

async function deleteJourney(id) {
  const journey = state.journeys.find((item) => item.id === id);
  if (!confirm(`Permanently delete ${journey.name} and all of its attendance, assignments, evaluations, photos, and audit history? This cannot be undone.`)) return;
  try { await api(`/api/admin/journeys/${id}`, mutation("DELETE")); toast("Journee permanently deleted."); await loadLibrary(); }
  catch (error) { toast(error.message, "error"); }
}

function createJourneyDialog() {
  openModal(`<form id="createJourneyForm"><p class="eyebrow">New workspace</p><h2>Create a Journee</h2><div class="stack"><label>Name<input name="name" required maxlength="200" placeholder="Journee 1"></label><label>Date<input name="date" type="date" required value="${new Date().toISOString().slice(0, 10)}"></label></div><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Create and open</button></div></form>`);
  $("#cancelModal").onclick = closeModal;
  $("#createJourneyForm").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const journey = await api("/api/admin/journeys", mutation("POST", { name: form.get("name"), event_date: form.get("date") }));
      closeModal();
      toast("Journee created.");
      await openJourney(journey.id);
    } catch (error) { toast(error.message, "error"); }
  };
}

async function duplicateJourney(id) {
  try {
    const journey = await api(`/api/admin/journeys/${id}/duplicate`, mutation());
    toast(`${journey.name} created.`);
    await loadLibrary();
  } catch (error) { toast(error.message, "error"); }
}

async function archiveJourney(id) {
  if (!confirm("Archive this Journee? Its evaluator link will stop accepting sessions.")) return;
  const journey = state.journeys.find((item) => item.id === id);
  try {
    await api(`/api/admin/journeys/${id}`, mutation("PATCH", { status: "archived", base_version: journey.version }));
    toast("Journee archived.");
    await loadLibrary();
  } catch (error) { toast(error.message, "error"); }
}

async function openJourney(id, section = "dashboard") {
  if (!guardDirty()) return;
  clearInterval(state.pollTimer);
  if (!state.journey || state.journey.id !== id) state.profileId = null;
  state.journey = await api(`/api/admin/journeys/${id}`);
  state.section = section;
  state.dirty = false;
  $("#libraryView").classList.add("hidden");
  $("#workspaceView").classList.remove("hidden");
  $("#journeyCrumb").textContent = state.journey.name;
  await renderSection();
  state.pollTimer = setInterval(async () => {
    if (document.hidden || state.dirty || !state.journey) return;
    if (["dashboard", "monitoring"].includes(state.section)) {
      try { await renderSection(true); } catch { /* a visible refresh remains available */ }
    }
    if (state.section === "attendance" && state.attendanceTab === "recruits") {
      try { await syncAdminRecruitAttendance(); } catch { /* keep the confirmed data already shown */ }
    }
    if (state.section === "settings" && Date.now() - state.lastProtectionPoll > 15000) {
      try { await refreshProtectionPanel(); } catch { /* keep the last visible status */ }
    }
  }, 5000);
}

async function refreshJourney() {
  if (!state.journey) return;
  state.journey = await api(`/api/admin/journeys/${state.journey.id}`);
  $("#journeyCrumb").textContent = state.journey.name;
}

async function returnToLibrary() {
  if (!guardDirty()) return;
  state.journey = null;
  state.dirty = false;
  await loadLibrary();
}

$("#workspaceNav").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-section]");
  if (!button || !guardDirty()) return;
  state.section = button.dataset.section;
  state.dirty = false;
  $("#sidebar").classList.remove("open");
  await renderSection();
});

async function renderSection(background = false) {
  if (!state.journey) return;
  $$("#workspaceNav button").forEach((button) => button.classList.toggle("active", button.dataset.section === state.section));
  if (!background) host.innerHTML = `<div class="loading-card">Loading ${h(state.section)}…</div>`;
  try {
    if (state.section === "dashboard") await renderDashboard();
    if (state.section === "attendance") await renderAttendance();
    if (state.section === "assignments") await renderAssignments();
    if (state.section === "monitoring") await renderMonitoring();
    if (state.section === "results") await renderResults();
    if (state.section === "profiles") await renderProfiles();
    if (state.section === "settings") await renderSettings();
    if (state.section === "permissions") await renderPermissions();
  } catch (error) {
    if (!background) host.innerHTML = `<div class="warning-box critical"><strong>Could not load this page.</strong><br>${h(error.message)}</div>`;
  }
}

function sectionHeading(eyebrow, title, subtitle, actions = "") {
  return `<div class="section-heading"><div><p class="eyebrow">${h(eyebrow)}</p><h1>${h(title)}</h1><p class="muted">${h(subtitle)}</p></div><div class="heading-actions">${actions}</div></div>`;
}

async function renderDashboard() {
  const data = await api(`/api/admin/journeys/${state.journey.id}/dashboard`);
  const presentRecruits = data.journey.presentRecruitCount;
  const presentEvaluators = data.journey.presentEvaluatorCount;
  const expectedSubmissions = data.activeMonitoring ? data.activeMonitoring.recruits.reduce((sum, item) => sum + item.expected, 0) : 0;
  const receivedSubmissions = data.activeMonitoring ? data.activeMonitoring.recruits.reduce((sum, item) => sum + item.submitted, 0) : 0;
  host.innerHTML = `${sectionHeading("Live operations", data.journey.name, `${data.journey.eventDate} · Updates every five seconds`, `<button class="button primary" id="goProtection">Event-day protection</button><button class="button ghost" id="refreshDashboard">Refresh</button>`)}
    <div class="metric-grid">
      <div class="metric-card"><small>Present recruits</small><strong>${presentRecruits}</strong><span class="subtle"> of ${data.journey.recruitCount}</span></div>
      <div class="metric-card"><small>Present evaluators</small><strong>${presentEvaluators}</strong><span class="subtle"> of ${data.journey.evaluatorCount}</span></div>
      <div class="metric-card"><small>Evaluator roles</small><div class="split"><span><strong>${data.overallEvaluators}</strong><small>Overall</small></span><span><strong>${data.dossardEvaluators}</strong><small>Dossard</small></span></div></div>
      <div class="metric-card"><small>Active activity</small><strong style="font-size:1.15rem">${data.journey.currentActivity ? h(statusLabel(data.journey.currentActivity)) : "None"}</strong><span class="subtle">${data.roomPlan ? `${data.roomPlan.rooms.length} rooms published` : "No room plan"}</span></div>
    </div>
    ${data.activeMonitoring ? `<div class="panel"><div class="panel-header"><div><h2>Live submission progress</h2><p class="muted">${receivedSubmissions} of ${expectedSubmissions} expected evaluations received.</p></div><button class="button ghost small" id="goMonitoring">Open monitor</button></div><div class="progress"><span style="width:${expectedSubmissions ? receivedSubmissions / expectedSubmissions * 100 : 0}%"></span></div></div>` : ""}
    ${data.warnings.length ? `<div class="panel"><h2>Unresolved warnings</h2>${data.warnings.map((warning) => `<div class="warning-box">${h(warning)}</div>`).join("")}</div>` : ""}
    <div class="panel"><div class="panel-header"><div><h2>Activity lifecycle</h2><p class="muted">Published assignments and operational state.</p></div></div><div class="activity-strip">${data.activities.map((activity) => `<div class="activity-mini"><strong>${h(activity.name)}</strong><span class="status-pill ${activity.status}">${h(statusLabel(activity.status))}</span><small>${activity.assignmentRoundId ? "Assignments published" : "No published assignment"}</small></div>`).join("")}</div></div>
    <div class="two-column"><div class="panel"><div class="panel-header"><h2>Provisional overall ranking</h2><button class="button ghost small" id="goResults">View all</button></div>${rankingTable(data.ranking)}</div>
    <div class="panel"><h2>Dimension averages /1</h2><div class="member-list">${dimensionOrder.map((code) => `<div class="member-chip"><span>${h(data.dimensionNames?.[code] || dimensionNames[code])}</span><strong>${fmt(data.dimensionAverages?.[code] || 0)}</strong></div>`).join("")}</div><h3 style="margin-top:20px">Activity averages /5</h3><div class="member-list">${Object.entries(data.activityAverages).map(([code, value]) => `<div class="member-chip"><span>${h(statusLabel(code))}</span><strong>${fmt(value)}</strong></div>`).join("")}</div><div style="margin-top:18px" class="inline-actions"><button class="button primary" id="quickAttendance">Attendance</button><button class="button secondary" id="quickAssignments">Assignments</button></div></div></div>`;
  $("#refreshDashboard").onclick = () => renderDashboard();
  $("#goProtection").onclick = () => switchSection("settings");
  if ($("#goMonitoring")) $("#goMonitoring").onclick = () => switchSection("monitoring");
  $("#goResults").onclick = () => switchSection("results");
  $("#quickAttendance").onclick = () => switchSection("attendance");
  $("#quickAssignments").onclick = () => switchSection("assignments");
  const metrics = $$(".metric-card", host);
  [0, 1, 2].forEach((index) => { if (metrics[index]) { metrics[index].classList.add("clickable-card"); metrics[index].onclick = () => { state.attendanceTab = index === 0 ? "recruits" : "evaluators"; switchSection("attendance"); }; } });
  if (metrics[3]) { metrics[3].classList.add("clickable-card"); metrics[3].onclick = () => switchSection(data.journey.currentActivity ? "monitoring" : "assignments"); }
  $$(".activity-mini", host).forEach((card, index) => { card.classList.add("clickable-card"); card.onclick = () => { state.monitoringActivity = data.activities[index].code; switchSection("monitoring"); }; });
  $$(".two-column .panel:first-child tbody tr", host).forEach((row, index) => { row.classList.add("clickable-row"); row.onclick = () => { state.profileId = data.ranking[index].recruitId; switchSection("profiles"); }; });
  const averageChips = $$(".two-column .panel:last-child .member-chip", host);
  averageChips.forEach((chip, index) => { chip.classList.add("clickable-card"); chip.onclick = () => { state.resultsActivity = index < dimensionOrder.length ? `dimension:${dimensionOrder[index]}` : `activity:${Object.keys(data.activityAverages)[index - dimensionOrder.length]}`; switchSection("results"); }; });
}

function switchSection(section) {
  state.section = section;
  state.dirty = false;
  renderSection();
}

function rankingTable(rows) {
  if (!rows.length) return `<div class="empty-state"><p>No present recruits yet.</p></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Rank</th><th>Recruit</th><th>Score /20</th><th>Color</th><th>Missing</th></tr></thead><tbody>${rows.map((row) => `<tr><td><span class="rank-number">${row.overallRank}</span></td><td>${h(row.name)}</td><td><strong>${fmt(row.overallScore)}</strong></td><td><span class="color-chip ${row.color}">${h(row.color)}</span></td><td>${row.missingCount}</td></tr>`).join("")}</tbody></table></div>`;
}

function makeAttendanceDraft() {
  const source = state.attendanceTab === "recruits" ? state.journey.recruits : state.journey.evaluators;
  state.attendanceDraft = Object.fromEntries(source.map((item) => [item.id, structuredClone(item)]));
  state.dirty = false;
}

function normalizedName(value) {
  return String(value || "").normalize("NFD").replace(/\p{Diacritic}/gu, "").toLocaleLowerCase().replace(/\s+/g, " ").trim();
}

function evaluatorAttendanceSort(a, b) {
  const group = (item) => item.present ? (item.role === "overall" ? 0 : 1) : (item.role === "overall" ? 2 : 3);
  return group(a) - group(b) || a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
}

function evaluatorSearchMatches(evaluators, query) {
  const normalized = normalizedName(query);
  if (!normalized) return [];
  return evaluators.filter((item) => normalizedName(`${item.name} ${item.fullName || ""}`).includes(normalized)).sort(evaluatorAttendanceSort);
}

function resolveEvaluatorSearch(evaluators, query) {
  const normalized = normalizedName(query);
  const nicknameExact = evaluators.find((item) => normalizedName(item.name) === normalized);
  if (nicknameExact) return nicknameExact;
  const fullNameExact = evaluators.filter((item) => item.fullName && normalizedName(item.fullName) === normalized);
  if (fullNameExact.length === 1) return fullNameExact[0];
  if (fullNameExact.length > 1) return null;
  const matches = evaluatorSearchMatches(evaluators, query);
  return matches.length === 1 ? matches[0] : null;
}

async function renderAttendance() {
  await refreshJourney();
  makeAttendanceDraft();
  drawAttendance();
}

function drawAttendance() {
  const isRecruit = state.attendanceTab === "recruits";
  const rows = Object.values(state.attendanceDraft || {}).filter((item) => item.active).sort((a, b) => isRecruit
    ? a.name.localeCompare(b.name)
    : evaluatorAttendanceSort(a, b));
  host.innerHTML = `${sectionHeading("Confirmed roster", "Attendance", isRecruit ? "Recruit changes save automatically and sync across devices." : "Evaluator edits stay on this device until Save attendance is pressed.", `<button class="button ghost" id="attendanceRefresh">${isRecruit ? "Sync now" : "Reload confirmed"}</button>`)}
    <div class="tabs"><button data-tab="recruits" class="${isRecruit ? "active" : ""}">Recruits</button><button data-tab="evaluators" class="${!isRecruit ? "active" : ""}">Evaluators</button></div>
    <div class="panel attendance-panel"><div class="panel-header"><div class="toolbar">${isRecruit ? `<input id="attendanceSearch" class="search-input" placeholder="Search by name or phone"><button class="button secondary" id="addPerson">+ Add recruit</button><button class="button ghost" id="importPeople">Import Excel/CSV</button><button class="button ghost" id="bulkPhotos">Bulk photos</button>` : `<div class="evaluator-quick-toggle"><input id="attendanceSearch" class="search-input" autocomplete="off" placeholder="Type evaluator name and press Enter" aria-label="Toggle evaluator attendance"><div id="attendanceSuggestions" class="search-suggestions" role="listbox"></div></div><button class="button secondary" id="addPerson">+ Add evaluator not listed</button>`}</div><span id="attendanceCount" class="subtle">${rows.filter((row) => row.present).length} present</span></div>
      ${isRecruit ? "" : `<p class="formula-note">Type a name and press Enter to toggle that evaluator Present/Absent. Ambiguous searches never change attendance.</p>`}
      <div id="attendanceTable"></div>
    </div>
    ${isRecruit ? `<div class="sticky-save auto-save-status"><span id="recruitSyncSummary" class="subtle">All recruit changes saved · checking for updates every 5 seconds</span></div>` : `<div class="sticky-save"><span id="unsavedLabel" class="subtle">No unsaved changes</span><button class="button ghost" id="discardAttendance" disabled>Discard</button><button class="button primary" id="saveAttendance" disabled>Save attendance</button></div>`}`;
  renderAttendanceTable(rows);
  $$(".tabs button", host).forEach((button) => button.onclick = () => {
    if (!guardDirty()) return;
    state.attendanceTab = button.dataset.tab;
    makeAttendanceDraft();
    drawAttendance();
  });
  $("#attendanceRefresh").onclick = isRecruit ? () => syncAdminRecruitAttendance(true) : renderAttendance;
  if (isRecruit) {
    $("#attendanceSearch").oninput = (event) => {
      const query = event.target.value.toLowerCase();
      renderAttendanceTable(rows.filter((item) => `${item.name} ${item.phoneNumber || ""}`.toLowerCase().includes(query)));
    };
  } else wireEvaluatorAttendanceSearch();
  $("#addPerson").onclick = () => addPersonDialog(isRecruit);
  if (isRecruit) {
    $("#importPeople").onclick = () => importDialog(state.attendanceTab);
    $("#bulkPhotos").onclick = bulkPhotosDialog;
  }
  if (!isRecruit) {
    $("#discardAttendance").onclick = () => { makeAttendanceDraft(); drawAttendance(); };
    $("#saveAttendance").onclick = saveAttendance;
  }
}

function evaluatorAttendanceRows() {
  return Object.values(state.attendanceDraft || {}).filter((item) => item.active).sort(evaluatorAttendanceSort);
}

function refreshEvaluatorAttendanceTable() {
  const rows = evaluatorAttendanceRows();
  renderAttendanceTable(rows);
  $("#attendanceCount").textContent = `${rows.filter((item) => item.present).length} present`;
}

function toggleEvaluatorAttendance(item) {
  item.present = !item.present;
  markAttendanceDirty();
  refreshEvaluatorAttendanceTable();
  toast(`${item.name} marked ${item.present ? "Present" : "Absent"}.`);
}

function wireEvaluatorAttendanceSearch() {
  const input = $("#attendanceSearch");
  const suggestions = $("#attendanceSuggestions");
  const evaluators = evaluatorAttendanceRows();
  const drawSuggestions = () => {
    const matches = evaluatorSearchMatches(evaluators, input.value).slice(0, 8);
    suggestions.innerHTML = matches.map((item) => `<button type="button" data-id="${item.id}" role="option"><span><strong>${h(item.name)}</strong><small>${h(item.fullName || "Full name not recorded")} · ${item.present ? "Present" : "Absent"}</small></span><span class="role-badge ${item.role}">${h(item.role)}</span></button>`).join("");
    suggestions.classList.toggle("visible", Boolean(input.value.trim() && matches.length));
    $$("button", suggestions).forEach((button) => button.onclick = () => {
      toggleEvaluatorAttendance(state.attendanceDraft[button.dataset.id]);
      input.value = "";
      suggestions.classList.remove("visible");
      input.focus();
    });
  };
  input.oninput = drawSuggestions;
  input.onkeydown = (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const item = resolveEvaluatorSearch(evaluators, input.value);
    if (!item) {
      const count = evaluatorSearchMatches(evaluators, input.value).length;
      return toast(count ? "More than one evaluator matches. Keep typing or select the correct suggestion." : "No evaluator matches that name. Use Add evaluator not listed if needed.", "error");
    }
    toggleEvaluatorAttendance(item);
    input.value = "";
    suggestions.classList.remove("visible");
    input.focus();
  };
}

function dateTimeInput(value) {
  if (!value) return "";
  const date = new Date(value);
  const formatter = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Beirut", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  return formatter.format(date).replace(" ", "T");
}

function nowBeirutInput() {
  return dateTimeInput(new Date().toISOString());
}

function renderAttendanceTable(rows) {
  const isRecruit = state.attendanceTab === "recruits";
  const target = $("#attendanceTable");
  const headers = isRecruit
    ? "<th>Photo</th><th>Name</th><th>Phone number</th><th>Date of birth</th><th>Present</th><th>Arrival time</th><th>Attendance comment</th><th>Photo action</th><th>Sync</th><th>Remove</th>"
    : "<th>Name</th><th>Present</th><th>Role</th><th>Mandatory room</th><th>Remove</th>";
  const body = rows.map((item) => {
    const photoUrl = `/api/admin/journeys/${state.journey.id}/recruits/${item.id}/photo`;
    const photo = item.hasPhoto
      ? `<button type="button" class="photo-zoom-trigger" data-photo-viewer data-photo-url="${photoUrl}" data-photo-name="${h(item.name)}"><img class="avatar" src="${photoUrl}" alt="${h(item.name)}"></button>`
      : `<span class="avatar placeholder">${h(item.name[0])}</span>`;
    if (isRecruit) return `<tr data-id="${item.id}" class="${item.present ? "" : "inactive"}"><td>${photo}</td><td><strong>${h(item.name)}</strong></td><td><input class="table-input phone-input" inputmode="tel" value="${h(item.phoneNumber || "")}" placeholder="Phone number"></td><td><input class="table-input dob-input" type="date" value="${h(item.dateOfBirth || "")}" aria-label="${h(item.name)} date of birth"></td><td><input class="attendance-check" type="checkbox" aria-label="${h(item.name)} present" ${item.present ? "checked" : ""}></td><td><input class="table-input arrival-input" type="datetime-local" value="${h(dateTimeInput(item.arrivalTime))}" ${item.present ? "" : "disabled"}></td><td><input class="table-input attendance-comment-input" value="${h(item.attendanceComment || "")}" placeholder="Reason for tardiness"></td><td><button class="button ghost small photo-button">${item.hasPhoto ? "Replace" : "Upload"}</button></td><td><span class="row-sync saved" data-sync-id="${item.id}">Saved</span></td><td><button class="button danger small delete-person">Delete</button></td></tr>`;
    return `<tr data-id="${item.id}" class="${item.present ? "" : "inactive"}"><td><strong>${h(item.name)}</strong>${item.mandatoryRoom && !item.present ? `<small class="danger-text"> Required evaluator is absent</small>` : ""}</td><td><input class="attendance-check" type="checkbox" aria-label="${h(item.name)} present" ${item.present ? "checked" : ""}></td><td><span class="role-badge ${item.role}">${h(item.role)}</span></td><td>${item.mandatoryRoom ? `Room ${item.mandatoryRoom}` : "—"}</td><td><button class="button danger small delete-person">Delete</button></td></tr>`;
  }).join("");
  target.innerHTML = `<div class="table-wrap"><table><thead><tr>${headers}</tr></thead><tbody>${body}</tbody></table></div>`;
  $$("tbody tr", target).forEach((row) => {
    const item = state.attendanceDraft[row.dataset.id];
    $(".delete-person", row).onclick = async () => {
      if (state.dirty) return toast("Save or discard the current attendance edits before deleting someone.", "error");
      if (isRecruit && state.recruitAttendanceSaves.size) return toast("Wait for the current recruit changes to finish saving.", "error");
      if (!confirm(`Delete ${item.name} from this Journee? Existing historical evaluations will be preserved.`)) return;
      try {
        const result = await api(`/api/admin/journeys/${state.journey.id}/${isRecruit ? "recruits" : "evaluators"}/${item.id}`, mutation("DELETE"));
        toast(result.disposition === "deleted" ? "Person deleted." : "Person removed; historical records were preserved.");
        await renderAttendance();
      } catch (error) { toast(error.message, "error"); }
    };
    $(".attendance-check", row).onchange = (event) => {
      item.present = event.target.checked;
      if (isRecruit) {
        const input = $(".arrival-input", row);
        input.disabled = !item.present;
        if (item.present && !input.value) input.value = nowBeirutInput();
        item.arrivalTime = item.present && input.value ? new Date(input.value).toISOString() : null;
        row.classList.toggle("inactive", !item.present);
        queueAdminRecruitSave(item, { present: item.present, arrival_time: item.arrivalTime });
      } else {
        markAttendanceDirty();
        refreshEvaluatorAttendanceTable();
      }
    };
    if (isRecruit) {
      $(".phone-input", row).oninput = (event) => { item.phoneNumber = event.target.value; queueAdminRecruitSave(item, { phone_number: item.phoneNumber || null }, 550); };
      $(".dob-input", row).onchange = (event) => { item.dateOfBirth = event.target.value || null; queueAdminRecruitSave(item, { date_of_birth: item.dateOfBirth }); };
      $(".arrival-input", row).onchange = (event) => { item.arrivalTime = event.target.value ? new Date(event.target.value).toISOString() : null; queueAdminRecruitSave(item, { arrival_time: item.arrivalTime }); };
      $(".attendance-comment-input", row).oninput = (event) => { item.attendanceComment = event.target.value; queueAdminRecruitSave(item, { attendance_comment: item.attendanceComment }, 550); };
      $(".photo-button", row).onclick = () => uploadSinglePhoto(item.id);
    }
  });
}

function adminRecruitSyncLabel(recruitId, text, kind = "saving") {
  const rowStatus = $(`[data-sync-id="${recruitId}"]`);
  if (rowStatus) {
    rowStatus.textContent = text;
    rowStatus.className = `row-sync ${kind}`;
  }
  const summary = $("#recruitSyncSummary");
  if (summary) summary.textContent = state.recruitAttendanceSaves.size
    ? `Saving ${state.recruitAttendanceSaves.size} recruit change${state.recruitAttendanceSaves.size === 1 ? "" : "s"}…`
    : "All recruit changes saved · checking for updates every 5 seconds";
}

function queueAdminRecruitSave(item, changes, delay = 0) {
  let entry = state.recruitAttendanceSaves.get(item.id);
  if (!entry) {
    entry = { changes: {}, timer: null, inFlight: false, retries: 0 };
    state.recruitAttendanceSaves.set(item.id, entry);
  }
  Object.assign(entry.changes, changes);
  entry.retries = 0;
  clearTimeout(entry.timer);
  adminRecruitSyncLabel(item.id, delay ? "Typing…" : "Saving…");
  entry.timer = setTimeout(() => runAdminRecruitSave(item.id), delay);
  const count = Object.values(state.attendanceDraft || {}).filter((value) => value.active && value.present).length;
  if ($("#attendanceCount")) $("#attendanceCount").textContent = `${count} present`;
}

function applyPendingRecruitChanges(item, changes) {
  if ("present" in changes) item.present = changes.present;
  if ("arrival_time" in changes) item.arrivalTime = changes.arrival_time;
  if ("phone_number" in changes) item.phoneNumber = changes.phone_number || "";
  if ("date_of_birth" in changes) item.dateOfBirth = changes.date_of_birth;
  if ("attendance_comment" in changes) item.attendanceComment = changes.attendance_comment || "";
}

async function runAdminRecruitSave(recruitId) {
  const entry = state.recruitAttendanceSaves.get(recruitId);
  const item = state.attendanceDraft?.[recruitId];
  if (!entry || !item || entry.inFlight || !Object.keys(entry.changes).length) return;
  entry.inFlight = true;
  const sent = entry.changes;
  entry.changes = {};
  adminRecruitSyncLabel(recruitId, "Saving…");
  try {
    const saved = await api(`/api/admin/journeys/${state.journey.id}/recruits/${recruitId}/attendance`, mutation("PATCH", { base_version: item.version, ...sent }));
    item.version = saved.version;
    const stored = state.journey.recruits.find((value) => value.id === recruitId);
    if (stored) Object.assign(stored, saved);
    entry.retries = 0;
    adminRecruitSyncLabel(recruitId, "Saved", "saved");
  } catch (error) {
    entry.changes = { ...sent, ...entry.changes };
    if (error.status === 409 && entry.retries < 3) {
      entry.retries += 1;
      try {
        const latest = await api(`/api/admin/journeys/${state.journey.id}`);
        const serverItem = latest.recruits.find((value) => value.id === recruitId && value.active);
        if (!serverItem) throw new Error("This recruit was removed on another device.");
        const pending = { ...entry.changes };
        Object.assign(item, serverItem);
        applyPendingRecruitChanges(item, pending);
        state.journey = latest;
        adminRecruitSyncLabel(recruitId, "Syncing…");
      } catch (refreshError) {
        entry.retries = 3;
        toast(refreshError.message, "error");
      }
    } else {
      entry.retries += 1;
      adminRecruitSyncLabel(recruitId, "Retrying…", "error");
      if (entry.retries === 1) toast(`${item.name}: ${error.message}`, "error");
    }
  } finally {
    entry.inFlight = false;
    if (Object.keys(entry.changes).length && entry.retries < 3) {
      clearTimeout(entry.timer);
      entry.timer = setTimeout(() => runAdminRecruitSave(recruitId), entry.retries ? 350 : 0);
    } else if (!Object.keys(entry.changes).length) {
      state.recruitAttendanceSaves.delete(recruitId);
      adminRecruitSyncLabel(recruitId, "Saved", "saved");
    } else {
      adminRecruitSyncLabel(recruitId, "Needs attention", "error");
    }
  }
}

async function syncAdminRecruitAttendance(force = false) {
  if (!state.journey || state.section !== "attendance" || state.attendanceTab !== "recruits") return;
  if (state.recruitAttendanceSaves.size) {
    if (force) toast("Waiting for the current recruit changes to finish saving.");
    return;
  }
  const focused = document.activeElement?.closest?.("#attendanceTable input");
  if (focused && !force) return;
  if (focused) focused.blur();
  const latest = await api(`/api/admin/journeys/${state.journey.id}`);
  const oldSignature = (state.journey.recruits || []).map((item) => `${item.id}:${item.version}:${item.active}`).sort().join("|");
  const newSignature = (latest.recruits || []).map((item) => `${item.id}:${item.version}:${item.active}`).sort().join("|");
  state.journey = latest;
  if (oldSignature !== newSignature) {
    makeAttendanceDraft();
    drawAttendance();
    toast("Recruit attendance updated from another device.");
  } else if ($("#recruitSyncSummary")) {
    $("#recruitSyncSummary").textContent = "All recruit changes saved · synced just now";
  }
}

function markAttendanceDirty() {
  setDirty(true);
  $("#unsavedLabel").innerHTML = `<span class="unsaved-dot"></span>Unsaved changes`;
  $("#discardAttendance").disabled = false;
  $("#saveAttendance").disabled = false;
}

async function saveAttendance() {
  const isRecruit = state.attendanceTab === "recruits";
  const items = Object.values(state.attendanceDraft).map((item) => isRecruit ? {
    id: item.id, present: item.present, arrival_time: item.present ? item.arrivalTime : null, phone_number: item.phoneNumber || null, date_of_birth: item.dateOfBirth || null, attendance_comment: item.attendanceComment || "", active: item.active, base_version: item.version,
  } : { id: item.id, present: item.present, role: item.role, active: item.active, base_version: item.version });
  try {
    await api(`/api/admin/journeys/${state.journey.id}/attendance/${state.attendanceTab}`, mutation("PUT", { items }));
    toast("Attendance saved and published.");
    state.dirty = false;
    await renderAttendance();
  } catch (error) { toast(error.message, "error"); }
}

function addPersonDialog(isRecruit) {
  if (isRecruit) return openRecruitDirectoryDialog();
  openModal(`<form id="personForm"><p class="eyebrow">Roster</p><h2>Add evaluator not in the default list</h2><div class="stack"><label>Username / nickname<input name="name" required maxlength="200"></label><label>Full name (optional)<input name="fullName" maxlength="200"></label><label>Phone number (optional)<input name="phoneNumber" inputmode="tel" maxlength="40"></label><label>Temporary password<input name="password" type="password" minlength="8" autocomplete="new-password" required></label><label>Role<select name="role"><option value="overall">Overall</option><option value="dossard">Dossard</option></select></label></div><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Add</button></div></form>`);
  $("#cancelModal").onclick = closeModal;
  $("#personForm").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = { name: form.get("name"), full_name: form.get("fullName") || null, phone_number: form.get("phoneNumber") || null, password: form.get("password"), role: form.get("role"), add_to_directory: true };
    try {
      await api(`/api/admin/journeys/${state.journey.id}/evaluators`, mutation("POST", body));
      closeModal(); toast("Person added to the roster."); await renderAttendance();
    } catch (error) { toast(error.message, "error"); }
  };
}

async function openRecruitDirectoryDialog() {
  openModal(`<div class="directory-dialog"><p class="eyebrow">Recruit roster</p><h2>Add recruit</h2><div class="loading-card">Loading the master recruit listâ€¦</div></div>`);
  try {
    const directory = await api("/api/admin/recruit-directory");
    let selected = null;
    $("#modalBody").innerHTML = `<div class="directory-dialog"><p class="eyebrow">Recruit roster</p><div class="panel-header"><h2>Add recruit</h2><button type="button" class="button ghost small" id="refreshRecruitDirectory">Refresh list</button></div>
      <form id="directoryRecruitForm" class="stack"><label>Search the master recruit list<div class="account-search-picker"><input id="recruitDirectorySearch" autocomplete="off" placeholder="Type a recruit name and press Enter" required><div id="recruitDirectorySuggestions" class="search-suggestions directory-suggestions" role="listbox"></div></div></label><div id="selectedRecruitDirectory" class="directory-selection empty">Select a recruit to copy their phone number and date of birth.</div><div class="modal-actions"><button type="button" class="button ghost cancel-recruit-modal">Cancel</button><button class="button primary" disabled>Add selected recruit</button></div></form>
      <div class="directory-divider"><span>Recruit not in the Google Sheet</span></div>
      <form id="manualRecruitForm" class="stack"><label>Full name<input name="name" required maxlength="200"></label><label>Phone number (optional)<input name="phone" inputmode="tel" maxlength="40"></label><label>Date of birth (optional)<input name="dateOfBirth" type="date"></label><div class="modal-actions"><button type="button" class="button ghost cancel-recruit-modal">Cancel</button><button class="button secondary">Add off-list recruit</button></div></form></div>`;
    modal.classList.add("wide");
    const input = $("#recruitDirectorySearch");
    const selection = $("#selectedRecruitDirectory");
    const addButton = $("#directoryRecruitForm button.primary");
    wireRecruitDirectoryPicker(input, $("#recruitDirectorySuggestions"), directory.items, { onSelect: (item) => {
      selected = item;
      addButton.disabled = false;
      selection.classList.remove("empty");
      selection.innerHTML = `<strong>${h(item.name)}</strong><span>${h(item.phoneNumber || "Phone not recorded")}</span><span>${h(item.dateOfBirthSource || item.dateOfBirth || "Date of birth not recorded")}</span>`;
    }});
    $$(".cancel-recruit-modal").forEach((button) => button.onclick = closeModal);
    $("#refreshRecruitDirectory").onclick = async () => {
      try {
        await api("/api/admin/recruit-directory/sync", mutation("POST"));
        toast("Master recruit list refreshed.");
        closeModal();
        await openRecruitDirectoryDialog();
      } catch (error) { toast(error.message, "error"); }
    };
    $("#directoryRecruitForm").onsubmit = async (event) => {
      event.preventDefault();
      if (!selected) return toast("Select a recruit from the master list.", "error");
      addButton.disabled = true;
      try {
        await api(`/api/admin/journeys/${state.journey.id}/recruits/from-directory`, mutation("POST", { directory_id: selected.id }));
        closeModal(); toast(`${selected.name} added with their saved details.`); await renderAttendance();
      } catch (error) { toast(error.message, "error"); addButton.disabled = false; }
    };
    $("#manualRecruitForm").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const button = event.currentTarget.querySelector("button.secondary");
      button.disabled = true;
      try {
        await api(`/api/admin/journeys/${state.journey.id}/recruits`, mutation("POST", { name: form.get("name"), phone_number: form.get("phone") || null, date_of_birth: form.get("dateOfBirth") || null }));
        closeModal(); toast("Off-list recruit added."); await renderAttendance();
      } catch (error) { toast(error.message, "error"); button.disabled = false; }
    };
    if (directory.stale) toast("Using the last saved recruit list because Google Sheets is temporarily unavailable.", "error");
  } catch (error) {
    $("#modalBody").innerHTML = `<div class="directory-dialog"><p class="eyebrow">Recruit roster</p><h2>Master list unavailable</h2><p class="danger-text">${h(error.message)}</p><p class="muted">Administrators can still add a recruit manually.</p><form id="manualRecruitForm" class="stack"><label>Full name<input name="name" required maxlength="200"></label><label>Phone number (optional)<input name="phone" inputmode="tel" maxlength="40"></label><label>Date of birth (optional)<input name="dateOfBirth" type="date"></label><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button secondary">Add off-list recruit</button></div></form></div>`;
    $("#cancelModal").onclick = closeModal;
    $("#manualRecruitForm").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const button = event.currentTarget.querySelector("button.secondary");
      button.disabled = true;
      try {
        await api(`/api/admin/journeys/${state.journey.id}/recruits`, mutation("POST", { name: form.get("name"), phone_number: form.get("phone") || null, date_of_birth: form.get("dateOfBirth") || null }));
        closeModal(); toast("Off-list recruit added."); await renderAttendance();
      } catch (problem) { toast(problem.message, "error"); button.disabled = false; }
    };
  }
}

function importDialog(kind) {
  openModal(`<form id="importForm"><p class="eyebrow">Bulk roster</p><h2>Import ${h(kind)}</h2><p class="muted">Use an .xlsx or UTF-8 .csv file with a Name column. Recruit files may include Phone Number and Date of Birth (YYYY-MM-DD or DD/MM/YYYY); evaluator files may include Role.</p><label>File<input name="file" type="file" accept=".xlsx,.xlsm,.csv" required></label><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Import</button></div></form>`);
  $("#cancelModal").onclick = closeModal;
  $("#importForm").onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const result = await api(`/api/admin/journeys/${state.journey.id}/import/${kind}`, { method: "POST", headers: { "X-CSRF-Token": state.csrf }, body: data });
      closeModal(); toast(`${result.created.length} imported; ${result.skipped.length} skipped.`); await renderAttendance();
    } catch (error) { toast(error.message, "error"); }
  };
}

function bulkPhotosDialog() {
  openModal(`<form id="photosForm"><p class="eyebrow">Photo matching</p><h2>Bulk upload recruit photos</h2><p class="muted">Name each image with the recruit's full name. Only unique matches are accepted.</p><label>Photos<input name="photos" type="file" accept="image/*" multiple required></label><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Match and upload</button></div></form>`);
  $("#cancelModal").onclick = closeModal;
  $("#photosForm").onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const result = await api(`/api/admin/journeys/${state.journey.id}/recruits/photos`, { method: "POST", headers: { "X-CSRF-Token": state.csrf }, body: data });
      closeModal(); toast(`${result.matched.length} photos matched; ${result.skipped.length} skipped.`); await renderAttendance();
    } catch (error) { toast(error.message, "error"); }
  };
}

function uploadSinglePhoto(recruitId) {
  const input = $("#hiddenFile");
  input.accept = "image/*";
  input.multiple = false;
  input.onchange = async () => {
    if (!input.files[0]) return;
    const data = new FormData();
    data.append("photo", input.files[0]);
    try {
      await api(`/api/admin/journeys/${state.journey.id}/recruits/${recruitId}/photo`, { method: "POST", headers: { "X-CSRF-Token": state.csrf }, body: data });
      toast("Photo saved."); await renderAttendance();
    } catch (error) { toast(error.message, "error"); }
    input.value = "";
  };
  input.click();
}

async function renderAssignments() {
  if (state.assignmentActivity === "simulation") state.assignmentActivity = "skills";
  const [publishedRooms, previewRooms, publishedRound, previewRound] = await Promise.all([
    api(`/api/admin/journeys/${state.journey.id}/rooms?status=published`),
    api(`/api/admin/journeys/${state.journey.id}/rooms?status=preview`),
    api(`/api/admin/journeys/${state.journey.id}/assignments/${state.assignmentActivity}?status=published`),
    api(`/api/admin/journeys/${state.journey.id}/assignments/${state.assignmentActivity}?status=preview`),
  ]);
  state.roomPlan = previewRooms || publishedRooms;
  state.assignmentRound = previewRound || publishedRound;
  const escapeState = state.journey.activities.find((item) => item.code === "escape_room");
  const roomsFrozen = escapeState && ["open", "closed"].includes(escapeState.status);
  const activityTabs = state.journey.activities.filter((activity) => activity.code !== "simulation").map((activity) => {
    const name = activity.code === "skills" ? "Skills & Simulation" : activity.name;
    return `<button data-activity="${activity.code}" class="${activity.code === state.assignmentActivity ? "active" : ""}">${h(name)}</button>`;
  }).join("");
  const assignmentName = state.assignmentActivity === "skills" ? "Skills & Simulation" : statusLabel(state.assignmentActivity);
  host.innerHTML = `${sectionHeading("Distribution", "Rooms & assignments", "Preview changes privately, review warnings, then publish atomically.")}
    <div class="panel"><div class="panel-header"><div><h2>Fixed room plan</h2><p class="muted">Used for Escape Room and Negotiation. Skills & Simulation use one global assignment for both activities.</p></div><div class="inline-actions"><label class="compact-field">Rooms<input id="roomCountInput" type="number" min="1" max="100" value="${state.journey.roomCount}"></label><button class="button ghost" id="saveRoomCount">Save room count</button><button class="button secondary" id="mandatoryRooms">Mandatory placements</button><button class="button ghost" id="previewRooms">${previewRooms ? "Regenerate preview" : "Generate preview"}</button>${previewRooms ? `<button class="button primary" id="publishRooms">${roomsFrozen ? "Publish override" : "Publish rooms"}</button>` : ""}</div></div>
      ${state.roomPlan ? renderRoomPlan(state.roomPlan) : `<div class="empty-state"><p>Confirm attendance, then generate the room plan.</p></div>`}
    </div>
    <div class="panel"><div class="tabs">${activityTabs}</div><div class="panel-header"><div><h2>${h(assignmentName)} assignments</h2><p class="muted">${previewRound ? "Showing private preview" : publishedRound ? "Showing evaluator-visible published version" : "No assignment generated"}.${state.assignmentActivity === "skills" ? " One publication applies the exact pairing to both activities." : ""}</p></div><div class="inline-actions"><button class="button ghost" id="previewAssignments">${previewRound ? "Regenerate preview" : "Generate preview"}</button>${previewRound ? `<button class="button primary" id="publishAssignments">${state.assignmentActivity === "skills" ? "Publish for both activities" : "Publish assignments"}</button>` : ""}</div></div>
      ${state.assignmentRound ? renderAssignmentRound(state.assignmentRound) : `<div class="empty-state"><p>Generate a reproducible assignment preview.</p></div>`}
    </div>`;
  $$(".tabs button", host).forEach((button) => button.onclick = () => { state.assignmentActivity = button.dataset.activity; renderAssignments(); });
  $("#saveRoomCount").onclick = async () => {
    const roomCount = Number($("#roomCountInput").value);
    if (!Number.isInteger(roomCount) || roomCount < 1 || roomCount > 100) return toast("Enter a room count from 1 to 100.", "error");
    if (roomCount === state.journey.roomCount) return toast("Room count is unchanged.");
    const save = (reason = "") => actionAndRefresh(`/api/admin/journeys/${state.journey.id}/rooms/count`, "PUT", { room_count: roomCount, reason }, "Room count saved. Generate a new room preview.", renderAssignments);
    if (publishedRooms) reasonDialog("Change published room count", "Explain why the published room structure is being replaced. Existing historical assignments remain unchanged.", save);
    else await save();
  };
  $("#previewRooms").onclick = async () => actionAndRefresh(`/api/admin/journeys/${state.journey.id}/rooms/preview`, "POST", {}, "Room preview generated.", renderAssignments);
  if ($("#publishRooms")) $("#publishRooms").onclick = async () => {
    if (roomsFrozen) {
      reasonDialog("Override frozen room plan", "This changes room membership for future rounds only. Existing assignments and evaluations remain unchanged.", (reason) => actionAndRefresh(`/api/admin/journeys/${state.journey.id}/rooms/${previewRooms.id}/publish-override`, "POST", { reason }, "Room override published for future rounds.", renderAssignments));
    } else {
      await actionAndRefresh(`/api/admin/journeys/${state.journey.id}/rooms/${previewRooms.id}/publish`, "POST", {}, "Room plan published.", renderAssignments);
    }
  };
  $("#previewAssignments").onclick = async () => actionAndRefresh(`/api/admin/journeys/${state.journey.id}/assignments/${state.assignmentActivity}/preview`, "POST", {}, "Assignment preview generated.", renderAssignments);
  if ($("#publishAssignments")) $("#publishAssignments").onclick = async () => actionAndRefresh(`/api/admin/journeys/${state.journey.id}/assignments/${previewRound.id}/publish`, "POST", {}, state.assignmentActivity === "skills" ? "Skills & Simulation assignments published to evaluators." : "Assignments published to evaluators.", renderAssignments);
  $("#mandatoryRooms").onclick = mandatoryRoomsDialog;
  if (previewRooms) wireRoomEditors(previewRooms);
  if (previewRound && previewRound.activityCode !== "simulation") wireAssignmentEditors(previewRound);
}

function warningHtml(warnings) {
  return (warnings || []).map((warning) => `<div class="warning-box">${h(warning)}</div>`).join("");
}

function renderRoomPlan(plan) {
  return `${warningHtml(plan.warnings)}<p class="subtle"><strong>${h(statusLabel(plan.status))} v${plan.version}</strong> · seed ${h(plan.seed)}</p><div class="room-grid">${plan.rooms.map((room) => `<article class="room-card"><h3>Room ${room.number}<span class="subtle">${room.recruits.length}R / ${room.evaluators.length}E</span></h3><small class="muted">Recruits</small><div class="member-list">${room.recruits.map((item) => `<div class="member-chip"><span>${h(item.name)}</span>${plan.status === "preview" ? roomSelector("recruit", item.id, room.number) : ""}</div>`).join("") || `<span class="subtle">None</span>`}</div><small class="muted" style="display:block;margin-top:12px">Evaluators</small><div class="member-list">${room.evaluators.map((item) => `<div class="member-chip"><span>${h(item.name)} ${item.mandatory ? "★" : ""}</span><span class="role-badge ${item.role}">${h(item.role)}</span>${plan.status === "preview" ? roomSelector("evaluator", item.id, room.number) : ""}</div>`).join("") || `<span class="subtle">None</span>`}</div></article>`).join("")}</div>${plan.status === "preview" ? `<div class="inline-actions" style="margin-top:14px"><button class="button secondary" id="saveRoomMoves">Save manual room moves</button></div>` : ""}`;
}

function roomSelector(type, id, selected) {
  return `<select class="room-move table-input" data-type="${type}" data-id="${id}">${Array.from({ length: state.journey.roomCount }, (_, index) => `<option value="${index + 1}" ${index + 1 === selected ? "selected" : ""}>Room ${index + 1}</option>`).join("")}</select>`;
}

function wireRoomEditors(plan) {
  const button = $("#saveRoomMoves");
  if (!button) return;
  button.onclick = async () => {
    const recruit_rooms = {}, evaluator_rooms = {};
    $$(".room-move", host).forEach((select) => (select.dataset.type === "recruit" ? recruit_rooms : evaluator_rooms)[select.dataset.id] = Number(select.value));
    await actionAndRefresh(`/api/admin/journeys/${state.journey.id}/rooms/${plan.id}`, "PUT", { recruit_rooms, evaluator_rooms }, "Manual room moves saved to preview.", renderAssignments);
  };
}

function renderAssignmentRound(round) {
  const editable = round.status === "preview" && round.activityCode !== "simulation";
  return `${warningHtml(round.warnings)}<p class="subtle"><strong>${h(statusLabel(round.status))} v${round.version}</strong> · seed ${h(round.seed)} · ${round.assignments.length} evaluator tasks</p><div class="assignment-list">${round.assignments.map((item) => `<div class="assignment-row ${item.repeatedPair ? "repeat" : ""}" data-evaluator="${item.evaluatorId}" data-recruit="${item.recruitId}" data-slot="${item.slot}" data-room="${item.roomNumber ?? ""}"><span><strong>${h(item.evaluatorName)}</strong> <span class="role-badge ${item.evaluatorRole}">${h(item.evaluatorRole)}</span></span><span>→</span><span><strong>${h(item.recruitName)}</strong> <small>slot ${item.slot}${item.roomNumber ? ` · room ${item.roomNumber}` : ""}</small></span>${editable ? `<button class="button ghost small remove-assignment">Remove</button>` : item.repeatedPair ? `<span class="status-pill warning">Repeat</span>` : ""}</div>`).join("")}</div>${editable ? `<div class="inline-actions" style="margin-top:14px"><button class="button secondary" id="addAssignment">Add pairing</button><button class="button ghost" id="saveAssignmentEdits">Save manual edits</button></div>` : round.activityCode === "simulation" && round.status === "preview" ? `<p class="subtle">Simulation is an exact, read-only copy of the published Skills pairing.</p>` : ""}`;
}

function wireAssignmentEditors(round) {
  $$(".remove-assignment", host).forEach((button) => button.onclick = () => button.closest(".assignment-row").remove());
  $("#addAssignment").onclick = () => {
    const evaluators = state.journey.evaluators.filter((item) => item.active && item.present);
    const recruits = state.journey.recruits.filter((item) => item.active && item.present);
    openModal(`<form id="addPairForm"><h2>Add manual pairing</h2><div class="stack"><label>Evaluator<select name="evaluator">${evaluators.map((item) => `<option value="${item.id}">${h(item.name)} (${h(item.role)})</option>`).join("")}</select></label><label>Recruit<select name="recruit">${recruits.map((item) => `<option value="${item.id}">${h(item.name)}</option>`).join("")}</select></label><label>Slot<select name="slot"><option value="1">Primary</option><option value="2">Secondary</option></select></label><label>Override reason (required for a forced repeat)<textarea name="reason"></textarea></label></div><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Add to preview</button></div></form>`);
    $("#cancelModal").onclick = closeModal;
    $("#addPairForm").onsubmit = (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const evaluator = evaluators.find((item) => item.id === form.get("evaluator"));
      const recruit = recruits.find((item) => item.id === form.get("recruit"));
      const slot = Number(form.get("slot"));
      const existing = $$(".assignment-row", host);
      if (existing.some((row) => row.dataset.evaluator === evaluator.id && row.dataset.recruit === recruit.id)) return toast("That evaluator–recruit pair already exists.", "error");
      const recruitRows = existing.filter((row) => row.dataset.recruit === recruit.id);
      if (recruitRows.length >= 2) return toast("A recruit cannot have more than two evaluators.", "error");
      if (recruitRows.some((row) => Number(row.dataset.slot) === slot)) return toast(`This recruit already has a slot ${slot} evaluator.`, "error");
      if (["escape_room", "negotiation"].includes(round.activityCode) && state.roomPlan) {
        const recruitRoom = state.roomPlan.rooms.find((room) => room.recruits.some((item) => item.id === recruit.id))?.number;
        const evaluatorRoom = state.roomPlan.rooms.find((room) => room.evaluators.some((item) => item.id === evaluator.id))?.number;
        if (!recruitRoom || recruitRoom !== evaluatorRoom) return toast("Escape Room and Negotiation pairings must stay inside the same room.", "error");
      }
      const container = document.createElement("div");
      container.className = "assignment-row";
      container.dataset.evaluator = evaluator.id; container.dataset.recruit = recruit.id; container.dataset.slot = slot; container.dataset.room = ""; container.dataset.reason = form.get("reason");
      container.innerHTML = `<span><strong>${h(evaluator.name)}</strong></span><span>→</span><span><strong>${h(recruit.name)}</strong> <small>slot ${h(form.get("slot"))}</small></span><button class="button ghost small remove-assignment">Remove</button>`;
      $(".assignment-list").append(container);
      $(".remove-assignment", container).onclick = () => container.remove();
      closeModal();
    };
  };
  $("#saveAssignmentEdits").onclick = async () => {
    const items = $$(".assignment-row", host).map((row) => ({ evaluator_id: row.dataset.evaluator, recruit_id: row.dataset.recruit, slot: Number(row.dataset.slot), room_number: row.dataset.room ? Number(row.dataset.room) : null, override_reason: row.dataset.reason || null }));
    await actionAndRefresh(`/api/admin/journeys/${state.journey.id}/assignments/${round.id}`, "PUT", { items }, "Manual assignment edits saved to preview.", renderAssignments);
  };
}

function mandatoryRoomsDialog() {
  const evaluators = state.journey.evaluators.filter((item) => item.active).sort(evaluatorAttendanceSort);
  const placements = Object.fromEntries(evaluators.filter((item) => item.mandatoryRoom).map((item) => [item.id, item.mandatoryRoom]));
  openModal(`<form id="mandatoryForm"><p class="eyebrow">Room constraints</p><h2>Mandatory evaluator placements</h2><p class="muted">Choose a room, type an evaluator, and press Enter. Enter assigns an unplaced evaluator, removes one already in that room, or moves one assigned to another room.</p><div class="mandatory-search-grid"><label>Target room<select id="mandatoryTargetRoom">${Array.from({ length: state.journey.roomCount }, (_, index) => `<option value="${index + 1}">Room ${index + 1}</option>`).join("")}</select></label><label>Required evaluator<div class="evaluator-quick-toggle"><input id="mandatorySearch" autocomplete="off" placeholder="Type evaluator name and press Enter"><div id="mandatorySuggestions" class="search-suggestions" role="listbox"></div></div></label></div><div id="mandatoryCount" class="formula-note"></div><div id="mandatoryList" class="mandatory-list"></div><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Save placements</button></div></form>`, { wide: true });
  $("#cancelModal").onclick = closeModal;
  const roomOptions = (selected) => `<option value="" ${selected ? "" : "selected"}>No mandatory room</option>${Array.from({ length: state.journey.roomCount }, (_, index) => `<option value="${index + 1}" ${selected === index + 1 ? "selected" : ""}>Room ${index + 1}</option>`).join("")}`;
  const drawList = () => {
    const count = Object.keys(placements).length;
    $("#mandatoryCount").textContent = `${count} mandatory evaluator${count === 1 ? "" : "s"} selected. Absent required evaluators will produce a room-planning warning.`;
    const ordered = [...evaluators].sort((a, b) => {
      const roomA = placements[a.id] || Number.MAX_SAFE_INTEGER;
      const roomB = placements[b.id] || Number.MAX_SAFE_INTEGER;
      const roleA = a.role === "overall" ? 0 : 1;
      const roleB = b.role === "overall" ? 0 : 1;
      return roomA - roomB || roleA - roleB || a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });
    $("#mandatoryList").innerHTML = ordered.map((item) => `<div class="mandatory-row ${item.present ? "" : "absent"}" data-id="${item.id}"><div><strong>${h(item.name)}</strong><small>${h(item.fullName || "Full name not recorded")} · ${item.present ? "Present" : "Absent"}</small></div><span class="role-badge ${item.role}">${h(item.role)}</span><select aria-label="Mandatory room for ${h(item.name)}">${roomOptions(placements[item.id])}</select></div>`).join("");
    $$(".mandatory-row", $("#mandatoryList")).forEach((row) => {
      $("select", row).onchange = (event) => {
        if (event.target.value) placements[row.dataset.id] = Number(event.target.value);
        else delete placements[row.dataset.id];
        drawList();
      };
    });
  };
  const input = $("#mandatorySearch");
  const suggestions = $("#mandatorySuggestions");
  const togglePlacement = (item) => {
    const room = Number($("#mandatoryTargetRoom").value);
    if (placements[item.id] === room) {
      delete placements[item.id];
      toast(`${item.name} removed from mandatory placements.`);
    } else {
      const moved = placements[item.id];
      placements[item.id] = room;
      toast(`${item.name} ${moved ? "moved" : "assigned"} to Room ${room}.${item.present ? "" : " This evaluator is currently absent."}`, item.present ? "success" : "error");
    }
    drawList();
    input.value = "";
    suggestions.classList.remove("visible");
    input.focus();
  };
  const drawSuggestions = () => {
    const matches = evaluatorSearchMatches(evaluators, input.value).slice(0, 8);
    suggestions.innerHTML = matches.map((item) => `<button type="button" data-id="${item.id}" role="option"><span><strong>${h(item.name)}</strong><small>${h(item.fullName || "Full name not recorded")} · ${item.present ? "Present" : "Absent"}${placements[item.id] ? ` · Room ${placements[item.id]}` : ""}</small></span><span class="role-badge ${item.role}">${h(item.role)}</span></button>`).join("");
    suggestions.classList.toggle("visible", Boolean(input.value.trim() && matches.length));
    $$("button", suggestions).forEach((button) => button.onclick = () => togglePlacement(evaluators.find((item) => item.id === button.dataset.id)));
  };
  input.oninput = drawSuggestions;
  input.onkeydown = (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const item = resolveEvaluatorSearch(evaluators, input.value);
    if (!item) {
      const count = evaluatorSearchMatches(evaluators, input.value).length;
      return toast(count ? "More than one evaluator matches. Keep typing or select a suggestion." : "No evaluator matches that name.", "error");
    }
    togglePlacement(item);
  };
  drawList();
  setTimeout(() => input.focus(), 0);
  $("#mandatoryForm").onsubmit = async (event) => {
    event.preventDefault();
    const items = Object.entries(placements).map(([evaluator_id, room_number]) => ({ evaluator_id, room_number }));
    try { await api(`/api/admin/journeys/${state.journey.id}/mandatory-rooms`, mutation("PUT", { items })); await refreshJourney(); closeModal(); toast("Mandatory placements saved."); } catch (error) { toast(error.message, "error"); }
  };
}

async function actionAndRefresh(url, method, body, message, refresh) {
  try { await api(url, mutation(method, body)); toast(message); await refreshJourney(); await refresh(); } catch (error) { toast(error.message, "error"); }
}

async function renderMonitoring() {
  await refreshJourney();
  const data = await api(`/api/admin/journeys/${state.journey.id}/monitoring/${state.monitoringActivity}`);
  const total = data.recruits.reduce((sum, item) => sum + item.expected, 0);
  const submitted = data.recruits.reduce((sum, item) => sum + item.submitted, 0);
  const remaining = Math.max(total - submitted, 0);
  const activity = state.journey.activities.find((item) => item.code === state.monitoringActivity);
  const skills = state.journey.activities.find((item) => item.code === "skills");
  const simulation = state.journey.activities.find((item) => item.code === "simulation");
  const pairStatus = skills.status === simulation.status ? skills.status : null;
  const pairButton = ["skills", "simulation"].includes(state.monitoringActivity) && pairStatus
    ? pairStatus === "not_started" ? `<button class="button primary" id="openPair">Open Skills & Simulation</button>`
      : pairStatus === "open" ? `<button class="button danger" id="closePair">Close both</button>`
      : `<button class="button secondary" id="reopenPair">Reopen both</button>` : "";
  const simulatorPanel = state.testToolsEnabled ? `<div class="panel testing-panel"><div class="panel-header"><div><p class="eyebrow">Development-only testing tool</p><h2>Evaluation simulator</h2><p class="muted">Acts as the assigned evaluators and submits valid randomized ${h(data.activityName || activity.name)} forms through the normal scoring and version-history workflow.</p></div><span class="status-pill warning">Not available in production</span></div>
    ${activity.status !== "open" ? `<div class="warning-box">Open ${h(data.activityName || activity.name)} before using the simulator.</div>` : total === 0 ? `<div class="empty-state"><p>No published evaluator tasks are assigned to this activity.</p></div>` : remaining === 0 ? `<div class="empty-state"><p>All ${total} assigned evaluations are already submitted.</p></div>` : `<div class="simulator-controls"><label class="activation-check"><input id="activateSimulator" type="checkbox" ${state.simulatorActivated ? "checked" : ""}> I understand this creates real test submissions in this Journee</label><label>Evaluations to complete<select id="simulatorCount" ${state.simulatorActivated ? "" : "disabled"}><option value="">All remaining (${remaining})</option>${Array.from({ length: remaining }, (_, index) => `<option value="${index + 1}">${index + 1} of ${remaining}</option>`).join("")}</select></label><button class="button danger" id="runSimulator" ${state.simulatorActivated ? "" : "disabled"}>Run simulated evaluations</button></div>`}
    <p class="subtle">Already submitted evaluations are never overwritten. Every generated form is labeled in its comment and audit history.</p></div>` : "";
  host.innerHTML = `${sectionHeading("Live submissions", "Activity control & monitoring", "Activities open one at a time, except Skills and Simulation which may run together.", `<button class="button ghost" id="refreshMonitoring">Refresh now</button>`)}
    <div class="activity-control-grid">${state.journey.activities.map((item) => `<article class="activity-control-card ${item.code === state.monitoringActivity ? "selected" : ""}"><div class="panel-header"><h3>${h(item.name)}</h3><span class="status-pill ${item.status}">${h(statusLabel(item.status))}</span></div><button class="button ghost small select-monitor" data-code="${item.code}">Monitor</button></article>`).join("")}</div>
    <div class="panel" style="margin-top:18px"><div class="panel-header"><div><h2>${h(data.activityName || activity.name)}</h2><p class="muted">${submitted} of ${total} expected evaluations received.</p></div><div class="inline-actions">${pairButton}${activity.status === "not_started" ? `<button class="button ghost" id="openActivity">Open only this activity</button>` : activity.status === "open" ? `<button class="button ghost" id="closeActivity">Close only this activity</button>` : `<button class="button ghost" id="reopenActivity">Reopen only this activity</button>`}</div></div><div class="progress"><span style="width:${total ? submitted / total * 100 : 0}%"></span></div>
      <div class="segmented"><button data-mode="recruits" class="${state.monitoringMode === "recruits" ? "active" : ""}">By recruit</button><button data-mode="evaluators" class="${state.monitoringMode === "evaluators" ? "active" : ""}">By evaluator</button></div>
      <div id="monitorTable" style="margin-top:14px">${monitorTable(data)}</div>
    </div>${simulatorPanel}`;
  $$(".select-monitor", host).forEach((button) => button.onclick = () => { state.monitoringActivity = button.dataset.code; state.simulatorActivated = false; renderMonitoring(); });
  $$(".segmented button", host).forEach((button) => button.onclick = () => { state.monitoringMode = button.dataset.mode; renderMonitoring(); });
  $("#refreshMonitoring").onclick = renderMonitoring;
  if ($("#openActivity")) $("#openActivity").onclick = () => lifecycleAction("open");
  if ($("#closeActivity")) $("#closeActivity").onclick = () => lifecycleAction("close");
  if ($("#reopenActivity")) $("#reopenActivity").onclick = () => reasonDialog("Reopen activity", "Explain why evaluator editing is being reopened.", (reason) => lifecycleAction("reopen", reason));
  if ($("#openPair")) $("#openPair").onclick = () => pairedLifecycleAction("open");
  if ($("#closePair")) $("#closePair").onclick = () => pairedLifecycleAction("close");
  if ($("#reopenPair")) $("#reopenPair").onclick = () => reasonDialog("Reopen Skills & Simulation", "Explain why both activities are being reopened.", (reason) => pairedLifecycleAction("reopen", reason));
  if ($("#activateSimulator")) $("#activateSimulator").onchange = (event) => {
    state.simulatorActivated = event.target.checked;
    $("#simulatorCount").disabled = !state.simulatorActivated;
    $("#runSimulator").disabled = !state.simulatorActivated;
  };
  if ($("#runSimulator")) $("#runSimulator").onclick = runEvaluationSimulator;
  $$(".submission-detail", host).forEach((button) => button.onclick = () => showSubmissionDetail(button.dataset.id));
  $$(".admin-evaluation", host).forEach((button) => button.onclick = () => showAdminEvaluationEditor(button.dataset.recruitId, button.dataset.activityCode));
  $$(".monitor-profile", host).forEach((button) => button.onclick = () => { state.profileId = button.dataset.id; switchSection("profiles"); });
}

async function runEvaluationSimulator() {
  if (!state.simulatorActivated) return;
  const rawCount = $("#simulatorCount").value;
  const count = rawCount ? Number(rawCount) : null;
  const label = count ? `${count} missing evaluation${count === 1 ? "" : "s"}` : "all remaining evaluations";
  if (!confirm(`Generate and submit ${label} for ${statusLabel(state.monitoringActivity)}? These are real test records and will affect monitoring and results.`)) return;
  const button = $("#runSimulator");
  button.disabled = true;
  try {
    const result = await api(`/api/admin/journeys/${state.journey.id}/testing/simulate/${state.monitoringActivity}`, mutation("POST", { count }, { "Idempotency-Key": uid() }));
    state.simulatorActivated = false;
    toast(`${result.completedCount} simulated evaluation${result.completedCount === 1 ? "" : "s"} submitted; ${result.remainingCount} remaining.`);
    await refreshJourney();
    await renderMonitoring();
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
  }
}

function monitorTable(data) {
  if (state.monitoringMode === "recruits") return `<div class="table-wrap"><table><thead><tr><th>Recruit</th><th>Expected</th><th>Received</th><th>Status</th><th>Evaluations</th></tr></thead><tbody>${data.recruits.map((item) => `<tr><td><button class="link-button monitor-profile" data-id="${item.id}">${h(item.name)}</button></td><td>${item.expected}</td><td>${item.submitted}</td><td><span class="status-pill ${(item.adminEvaluation || (item.submitted === item.expected && item.expected)) ? "completed" : "warning"}">${item.adminEvaluation ? "Admin grade" : item.submitted === item.expected && item.expected ? "Complete" : "Incomplete"}</span></td><td><div class="inline-actions"><button class="button primary small admin-evaluation" data-recruit-id="${item.id}" data-activity-code="${data.activityCode}">${item.adminEvaluation ? "Edit admin evaluation" : "Add admin evaluation"}</button>${item.tasks.filter((task) => task.submissionId).map((task) => `<button class="button ghost small submission-detail" data-id="${task.submissionId}">Edit ${h(task.evaluatorName)}</button>`).join(" ")}</div></td></tr>`).join("")}</tbody></table></div>`;
  if (state.monitoringMode === "recruits") return `<div class="table-wrap"><table><thead><tr><th>Recruit</th><th>Expected</th><th>Received</th><th>Status</th><th>Evaluations</th></tr></thead><tbody>${data.recruits.map((item) => `<tr><td>${h(item.name)}</td><td>${item.expected}</td><td>${item.submitted}</td><td><span class="status-pill ${item.submitted === item.expected && item.expected ? "completed" : "warning"}">${item.submitted === item.expected && item.expected ? "Complete" : "Incomplete"}</span></td><td>${item.tasks.filter((task) => task.submissionId).map((task) => `<button class="button ghost small submission-detail" data-id="${task.submissionId}">Edit ${h(task.evaluatorName)}</button>`).join(" ") || "—"}</td></tr>`).join("")}</tbody></table></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Evaluator</th><th>Role</th><th>Assigned recruit(s)</th><th>Received</th><th>Evaluations</th></tr></thead><tbody>${data.evaluators.map((item) => `<tr><td>${h(item.name)}</td><td><span class="role-badge ${item.role}">${h(item.role)}</span></td><td>${item.tasks.map((task) => h(task.recruitName)).join(", ")}</td><td>${item.tasks.filter((task) => task.submitted).length}/${item.tasks.length}</td><td>${item.tasks.filter((task) => task.submissionId).map((task) => `<button class="button ghost small submission-detail" data-id="${task.submissionId}">Edit ${h(task.recruitName)}</button>`).join(" ") || "—"}</td></tr>`).join("")}</tbody></table></div>`;
}

function evaluationCriterionEditor(criterion, value) {
  if (criterion.inputType === "duration") return durationPickerHtml(criterion.key, value);
  if (criterion.inputType === "integer") return `<input class="whole-number-input" name="${criterion.key}" type="number" min="0" step="1" inputmode="numeric" value="${h(value ?? "")}" required>`;
  return `<input class="grade-input" name="${criterion.key}" type="number" min="0" max="5" step="0.1" inputmode="decimal" value="${h(value ?? "")}" required>`;
}

async function showSubmissionDetail(submissionId) {
  try {
    const detail = await api(`/api/admin/journeys/${state.journey.id}/submissions/${submissionId}`);
    const values = detail.activityCode === "sport" ? detail.raw : detail.responses;
    const activityState = state.journey.activities.find((item) => item.code === detail.activityCode)?.status || "not_started";
    openModal(`<form id="correctionForm"><p class="eyebrow">Edit evaluation</p><h2>${h(detail.recruitName)} · ${h(detail.activityName)}</h2><p class="muted">Evaluator: ${h(detail.evaluatorName)} · Version ${detail.version} · Current score ${fmt(detail.score)} /5</p><div class="warning-box">This is an administrative correction. It is allowed while the activity is ${h(statusLabel(activityState))} and creates a permanent new version in the audit history.</div><div class="stack evaluation-editor-scroll">${detail.rubric.criteria.map((criterion) => `<label>${h(criterion.name)}<small class="muted">${h(criterion.explanation)}</small>${evaluationCriterionEditor(criterion, values[criterion.key])}</label>`).join("")}<label>Comments<textarea name="comments">${h(detail.comments)}</textarea></label><label>Required reason for change<textarea name="reason" required placeholder="Explain why this evaluation is being changed"></textarea></label></div><h3 style="margin-top:16px">Edit history</h3><div class="audit-list">${detail.history.map((item) => `<div class="audit-item"><small>${localDateTime(item.createdAt)}</small><span>${h(item.actorName)} · v${item.version}</span><span>${fmt(item.score)} /5${item.reason ? ` · ${h(item.reason)}` : ""}</span></div>`).join("")}</div><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Save evaluation changes</button></div></form>`, { wide: true });
    $("#cancelModal").onclick = closeModal;
    const correctionForm = $("#correctionForm");
    wireDurationPickers(correctionForm);
    wireBoundedNumberInputs(correctionForm);
    correctionForm.onsubmit = async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"], button.button.primary');
      const form = new FormData(event.currentTarget);
      const responses = {}, raw = {};
      for (const criterion of detail.rubric.criteria) {
        if (detail.activityCode === "sport") raw[criterion.key] = form.get(criterion.key);
        else responses[criterion.key] = Number(form.get(criterion.key));
      }
      button.disabled = true;
      try {
        await api(`/api/admin/journeys/${state.journey.id}/submissions/${submissionId}/correct`, mutation("POST", { responses, raw, comments: form.get("comments"), reason: form.get("reason"), client_version: detail.version }));
        closeModal();
        toast("Evaluation saved as a new audited version.");
        await refreshJourney();
        await renderSection();
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
      }
    };
  } catch (error) { toast(error.message, "error"); }
}

async function lifecycleAction(action, reason = "") {
  if (action === "close" && !confirm("Close this activity and lock evaluator editing?")) return;
  await actionAndRefresh(`/api/admin/journeys/${state.journey.id}/activities/${state.monitoringActivity}/${action}`, "POST", { reason }, `Activity ${action === "reopen" ? "reopened" : `${action}ed`}.`, renderMonitoring);
}

async function pairedLifecycleAction(action, reason = "") {
  if (action === "close" && !confirm("Close Skills and Simulation and lock both sets of evaluations?")) return;
  await actionAndRefresh(`/api/admin/journeys/${state.journey.id}/activities/skills-simulation/${action}`, "POST", { reason }, `Skills and Simulation ${action === "reopen" ? "reopened" : `${action}ed`} together.`, renderMonitoring);
}

function reasonDialog(title, text, callback) {
  openModal(`<form id="reasonForm"><h2>${h(title)}</h2><p class="muted">${h(text)}</p><label>Required reason<textarea name="reason" required></textarea></label><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Confirm</button></div></form>`);
  $("#cancelModal").onclick = closeModal;
  $("#reasonForm").onsubmit = (event) => { event.preventDefault(); const reason = new FormData(event.currentTarget).get("reason"); closeModal(); callback(reason); };
}

async function renderResults() {
  const results = await api(`/api/admin/journeys/${state.journey.id}/results`);
  const dimensionTabs = dimensionOrder.map((code) => `<button data-result="dimension:${code}" class="${state.resultsActivity === `dimension:${code}` ? "active" : ""}">${h(results.dimensionNames?.[code] || dimensionNames[code])}</button>`);
  const activityTabs = state.journey.activities.map((item) => `<button data-result="activity:${item.code}" class="${state.resultsActivity === `activity:${item.code}` ? "active" : ""}">${h(item.name)}</button>`);
  const tabs = [`<button data-result="overall" class="${state.resultsActivity === "overall" ? "active" : ""}">Overall /20</button>`, ...dimensionTabs, ...activityTabs].join("");
  let rows = [...results.rows];
  let table;
  if (state.resultsActivity === "overall") {
    rows.sort((a, b) => a.overallRank - b.overallRank);
    table = overallResultsTable(rows);
  } else if (state.resultsActivity.startsWith("dimension:")) {
    const code = state.resultsActivity.split(":")[1];
    rows.sort((a, b) => a.dimensions[code].rank - b.dimensions[code].rank);
    table = dimensionResultsTable(rows, code, results.dimensionAverages[code]);
  } else {
    const code = state.resultsActivity.replace("activity:", "");
    rows.sort((a, b) => a.activities[code].rank - b.activities[code].rank);
    table = activityResultsTable(rows, code, results.activityAverages[code]);
  }
  host.innerHTML = `${sectionHeading("Scores", "Results & rankings", "Overall /20 is calculated from six dimensions plus the general assessment. Activity statistics remain available in the activity tabs.", `<a class="button ghost" href="/api/admin/journeys/${state.journey.id}/results.csv">Quick CSV</a><a class="button primary" href="/api/admin/journeys/${state.journey.id}/export.xlsx">Interactive management report</a>`)}<div class="panel"><p class="formula-note">${h(results.formula)}</p><div class="tabs">${tabs}</div>${table}</div>`;
  $(".section-heading .muted", host).textContent = "Rankings, dimensions, and activity statistics.";
  $(".panel > .formula-note", host)?.remove();
  $$(".tabs button", host).forEach((button) => button.onclick = () => { state.resultsActivity = button.dataset.result; renderResults(); });
  $$(".result-profile", host).forEach((button) => button.onclick = () => { state.profileId = button.dataset.id; switchSection("profiles"); });
}

function overallResultsTable(rows) {
  return `<div class="table-wrap"><table><thead><tr><th>Rank</th><th>Recruit</th><th>Overall /20</th>${dimensionOrder.map((code) => `<th>${h(dimensionNames[code])} /1</th>`).join("")}<th>General /1</th><th>Color</th><th>Missing</th></tr></thead><tbody>${rows.map((row) => `<tr><td><span class="rank-number">${row.overallRank}</span></td><td><button type="button" class="button ghost small result-profile" data-id="${row.recruitId}">${h(row.name)}</button></td><td><strong>${fmt(row.overallScore)}</strong></td>${dimensionOrder.map((code) => `<td>${fmt(row.dimensions[code].score)}</td>`).join("")}<td>${fmt(row.generalAverage)}</td><td><span class="color-chip ${row.color}">${h(row.color)}</span></td><td>${row.missingCount}</td></tr>`).join("")}</tbody></table></div>`;
}

function dimensionResultsTable(rows, code, average) {
  return `<p class="muted">Dimension average: <strong>${fmt(average)} /1</strong></p><div class="table-wrap"><table><thead><tr><th>Rank</th><th>Recruit</th><th>Grade /1</th><th>Coverage</th><th>Status</th></tr></thead><tbody>${rows.map((row) => { const item = row.dimensions[code]; return `<tr><td><span class="rank-number">${item.rank}</span></td><td><button type="button" class="button ghost small result-profile" data-id="${row.recruitId}">${h(row.name)}</button></td><td><strong>${fmt(item.score)}</strong></td><td>${Math.round((item.availableWeight || 0) * 100)}%</td><td><span class="status-pill ${item.complete ? "completed" : "warning"}">${item.complete ? "Complete" : "Incomplete"}</span></td></tr>`; }).join("")}</tbody></table></div>`;
}

function activityResultsTable(rows, code, average) {
  return `<p class="muted">Activity average: <strong>${fmt(average)} /5</strong></p><div class="table-wrap"><table><thead><tr><th>Rank</th><th>Recruit</th><th>Grade /5</th><th>Submissions</th><th>Status</th></tr></thead><tbody>${rows.map((row) => { const item = row.activities[code]; return `<tr><td><span class="rank-number">${item.rank}</span></td><td><button type="button" class="button ghost small result-profile" data-id="${row.recruitId}">${h(row.name)}</button></td><td><strong>${fmt(item.score)}</strong></td><td>${item.submitted}/${item.expected}</td><td><span class="status-pill ${item.complete ? "completed" : "warning"}">${item.complete ? "Complete" : "Incomplete"}</span></td></tr>`; }).join("")}</tbody></table></div>`;
}

async function renderProfiles() {
  const recruits = state.journey.recruits.filter((item) => item.active).sort((a, b) => a.name.localeCompare(b.name));
  if (!recruits.some((item) => item.id === state.profileId)) state.profileId = recruits[0]?.id || null;
  const profile = state.profileId ? await api(`/api/admin/journeys/${state.journey.id}/recruits/${state.profileId}/profile`) : null;
  host.innerHTML = `${sectionHeading("Individual record", "Recruit profile", "Grades, rankings, comments, and correction history in one view.", `<select id="profileSelect">${recruits.map((item) => `<option value="${item.id}" ${item.id === state.profileId ? "selected" : ""}>${h(item.name)}</option>`).join("")}</select>`)}${profile ? profileHtml(profile) : `<div class="empty-state"><h2>No recruits</h2><p>Add a recruit to begin.</p></div>`}`;
  if ($("#profileSelect")) $("#profileSelect").onchange = (event) => { if (!guardDirty()) return; state.profileId = event.target.value; state.dirty = false; renderProfiles(); };
  if (profile) wireProfile(profile);
}

function profileHtml(profile) {
  const result = profile.result || { activities: {}, dimensions: {}, overallScore: 0, overallRank: "—", color: "red", missingCount: 8 };
  const arrival = profile.recruit.arrivalTime ? dateTimeInput(profile.recruit.arrivalTime).split("T")[1] : "Not recorded";
  return `<div class="panel"><div class="profile-header">${profile.photoUrl ? `<button type="button" class="photo-zoom-trigger profile-photo-trigger" data-photo-viewer data-photo-url="${profile.photoUrl}" data-photo-name="${h(profile.recruit.name)}"><img class="profile-photo" src="${profile.photoUrl}" alt="${h(profile.recruit.name)}"></button>` : `<span class="profile-photo avatar placeholder">${h(profile.recruit.name[0])}</span>`}<div><h2>${h(profile.recruit.name)}</h2><p class="muted">${h(profile.recruit.phoneNumber || "No phone number")} · ${profile.recruit.dateOfBirth ? `Date of birth: ${h(profile.recruit.dateOfBirth)}` : "Date of birth not recorded"} · ${profile.recruit.present ? "Present" : "Absent"}</p><p class="profile-arrival"><strong>Arrival time:</strong> ${h(arrival)}</p></div><div class="profile-score-group"><div class="grade-orb ${result.color}"><strong>${h(result.color)}</strong><small>Color grade</small></div><div class="score-orb"><div><strong>${fmt(result.overallScore)}</strong><small>/20 · rank ${result.overallRank ?? "—"}</small></div></div></div></div></div>
    <div class="panel"><div class="panel-header"><div><h2>Dimension performance</h2><p class="muted">These six grades, plus the general assessment, determine the overall /20. Select a dimension to inspect every criterion and evaluator grade.</p></div></div><div class="profile-performance"><div class="radar-wrap">${dimensionRadar(result)}</div><div class="profile-dimension-grid">${dimensionOrder.map((code) => { const item = result.dimensions?.[code] || { score: 0, rank: "—", complete: false }; return `<button type="button" class="profile-activity dimension-card" data-dimension="${code}" aria-label="View ${h(dimensionNames[code])} criterion grading"><small>${h(dimensionNames[code])}</small><strong>${fmt(item.score)} /1</strong><small>Rank ${item.rank ?? "—"} · ${item.complete ? "Complete" : "Incomplete"}</small><span class="dimension-card-action">View criteria →</span></button>`; }).join("")}</div></div></div>
    <div class="panel"><h2>Activity performance</h2><p class="muted">Select an activity to inspect and edit its evaluator submissions.</p><div class="profile-performance"><div class="radar-wrap">${activityRadar(result)}</div><div class="profile-activity-grid">${state.journey.activities.map((activity) => { const item = result.activities[activity.code] || { score: 0, rank: "—", submitted: 0, expected: 0 }; return `<button type="button" class="profile-activity profile-activity-button" data-activity-code="${activity.code}"><small>${h(activity.name)}</small><strong>${fmt(item.score)} /5</strong><small>Rank ${item.rank ?? "—"} · ${item.submitted}/${item.expected}</small><span class="dimension-card-action">View evaluations →</span></button>`; }).join("")}</div></div></div>
    <div class="two-column"><div class="panel"><h2>General assessment</h2><form id="profileForm" class="stack"><div class="three-column"><label>Punctuality<input name="punctuality" type="number" min="0" max="1" step="0.1" value="${profile.assessment.punctuality ?? ""}"></label><label>Respect to us<input name="respect" type="number" min="0" max="1" step="0.1" value="${profile.assessment.respect ?? ""}"></label><label>Seriousness<input name="seriousness" type="number" min="0" max="1" step="0.1" value="${profile.assessment.seriousness ?? ""}"></label></div><label>General admin comment<textarea name="comment">${h(profile.assessment.comment)}</textarea></label><label>Notes<textarea name="notes" rows="5" placeholder="Add private administrative notes about this recruit">${h(profile.assessment.notes)}</textarea></label><div class="inline-actions"><button type="button" class="button ghost" id="discardProfile">Discard</button><button class="button primary">Save profile</button></div></form></div><div class="panel"><h2>Completion</h2><p><strong>${result.missingCount}</strong> missing component${result.missingCount === 1 ? "" : "s"}</p></div></div>
    <div class="panel"><h2>Evaluator breakdown</h2>${Object.entries(profile.evaluations).map(([code, entries]) => `<h3 style="margin-top:16px">${h(statusLabel(code))}</h3>${entries.length ? `<div class="table-wrap"><table><thead><tr><th>Evaluator</th><th>Role</th><th>Score</th><th>Status</th><th>Comment</th><th>Control</th></tr></thead><tbody>${entries.map((entry) => `<tr><td>${h(entry.evaluatorName)}</td><td>${h(entry.evaluatorRole)}</td><td>${entry.submission ? fmt(entry.submission.score) : "—"}</td><td>${entry.submission ? h(statusLabel(entry.submission.status)) : "Missing"}</td><td>${h(entry.submission?.comments || "")}</td><td>${entry.submission ? `<button type="button" class="button secondary small submission-detail" data-id="${entry.submission.id}">Edit evaluation</button>` : "—"}</td></tr>`).join("")}</tbody></table></div>` : `<p class="subtle">No published evaluator assignment.</p>`}`).join("")}</div>
    <div class="panel"><h2>Profile audit history</h2>${profile.history.length ? `<div class="audit-list">${profile.history.map(auditItem).join("")}</div>` : `<p class="muted">No profile changes yet.</p>`}</div>`;
}

function activityRadar(result) {
  const activities = state.journey.activities;
  const cx = 160, cy = 145, radius = 104;
  const point = (index, value) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / activities.length;
    return [cx + Math.cos(angle) * radius * value, cy + Math.sin(angle) * radius * value];
  };
  const polygon = (value) => activities.map((_, index) => point(index, value).join(",")).join(" ");
  const dataPoints = activities.map((activity, index) => point(index, Math.max(0, Math.min(5, Number(result.activities?.[activity.code]?.score || 0))) / 5));
  return `<svg class="activity-radar" viewBox="0 0 320 300" role="img" aria-label="Radar chart of activity grades out of five">
    ${[.2,.4,.6,.8,1].map((level) => `<polygon class="radar-grid" points="${polygon(level)}"></polygon>`).join("")}
    ${activities.map((_, index) => { const p = point(index, 1); return `<line class="radar-axis" x1="${cx}" y1="${cy}" x2="${p[0]}" y2="${p[1]}"></line>`; }).join("")}
    <polygon class="radar-data" points="${dataPoints.map((p) => p.join(",")).join(" ")}"></polygon>
    ${dataPoints.map((p) => `<circle class="radar-dot" cx="${p[0]}" cy="${p[1]}" r="4"></circle>`).join("")}
    ${activities.map((activity, index) => { const p = point(index, 1.2); const score = fmt(result.activities?.[activity.code]?.score || 0); return `<text class="radar-label" x="${p[0]}" y="${p[1]}" text-anchor="middle" dominant-baseline="middle"><tspan x="${p[0]}">${h(activity.name)}</tspan><tspan x="${p[0]}" dy="15">${score}/5</tspan></text>`; }).join("")}
  </svg>`;
}

function dimensionRadar(result) {
  const items = dimensionOrder.map((code) => ({ code, name: dimensionNames[code], score: Number(result.dimensions?.[code]?.score || 0) }));
  const cx = 180, cy = 155, radius = 104;
  const point = (index, value) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / items.length;
    return [cx + Math.cos(angle) * radius * value, cy + Math.sin(angle) * radius * value];
  };
  const polygon = (value) => items.map((_, index) => point(index, value).join(",")).join(" ");
  const dataPoints = items.map((item, index) => point(index, Math.max(0, Math.min(1, item.score))));
  return `<svg class="activity-radar" viewBox="0 0 360 320" role="img" aria-label="Radar chart of the six dimension grades out of one">
    ${[.2,.4,.6,.8,1].map((level) => `<polygon class="radar-grid" points="${polygon(level)}"></polygon>`).join("")}
    ${items.map((_, index) => { const p = point(index, 1); return `<line class="radar-axis" x1="${cx}" y1="${cy}" x2="${p[0]}" y2="${p[1]}"></line>`; }).join("")}
    <polygon class="radar-data" points="${dataPoints.map((p) => p.join(",")).join(" ")}"></polygon>
    ${dataPoints.map((p) => `<circle class="radar-dot" cx="${p[0]}" cy="${p[1]}" r="4"></circle>`).join("")}
    ${items.map((item, index) => { const p = point(index, 1.24); return `<text class="radar-label" x="${p[0]}" y="${p[1]}" text-anchor="middle" dominant-baseline="middle"><tspan x="${p[0]}">${h(item.name)}</tspan><tspan x="${p[0]}" dy="15">${fmt(item.score)}/1</tspan></text>`; }).join("")}
  </svg>`;
}

function showActivityBreakdown(profile, code) {
  const activity = state.journey.activities.find((item) => item.code === code);
  const entries = profile.evaluations?.[code] || [];
  const result = profile.result?.activities?.[code] || { score: 0, rank: null, submitted: 0, expected: entries.length };
  openModal(`<div><p class="eyebrow">Activity grading</p><div class="panel-header"><div><h2>${h(profile.recruit.name)} · ${h(activity?.name || statusLabel(code))}</h2><p class="muted">Every evaluator submission contributing to this activity grade.</p></div><div class="dimension-modal-score"><strong>${fmt(result.score)} /5</strong><small>Rank ${result.rank ?? "—"} · ${result.submitted}/${result.expected} submitted</small></div></div><div class="dimension-breakdown-scroll">${entries.length ? entries.map((entry) => `<article class="dimension-criterion"><div class="panel-header"><div><strong>${h(entry.evaluatorName)}</strong> <span class="role-badge ${h(entry.evaluatorRole)}">${h(entry.evaluatorRole)}</span><p class="muted">${entry.submission ? h(entry.submission.comments || "No comment") : "Evaluation not submitted"}</p></div><div class="criterion-evaluator-actions"><div class="criterion-grade"><strong>${entry.submission ? `${fmt(entry.submission.score)} /5` : "Missing"}</strong><small>${entry.submission ? h(statusLabel(entry.submission.status)) : "No submission"}</small></div>${entry.submission ? `<button type="button" class="button secondary small edit-activity-submission" data-id="${entry.submission.id}">Edit evaluation</button>` : ""}</div></div></article>`).join("") : `<div class="empty-state"><p>No published evaluator assignments.</p></div>`}</div><div class="modal-actions"><button type="button" class="button primary" id="cancelModal">Close</button></div></div>`, { wide: true });
  $("#cancelModal").onclick = closeModal;
  $(".modal-actions", modal).insertAdjacentHTML("afterbegin", `<button type="button" class="button primary" id="activityAdminEvaluation">${profile.adminEvaluations?.[code] ? "Edit official admin evaluation" : "Add official admin evaluation"}</button>`);
  $("#activityAdminEvaluation").onclick = () => { closeModal(); showAdminEvaluationEditor(profile.recruit.id, code); };
  $$(".edit-activity-submission", modal).forEach((button) => button.onclick = () => { closeModal(); showSubmissionDetail(button.dataset.id); });
}

function showDimensionBreakdown(profile, code) {
  const breakdown = profile.dimensionBreakdowns?.[code];
  if (!breakdown) {
    toast("The dimension breakdown is not available.", "error");
    return;
  }
  const activitySections = breakdown.activities.map((activity) => `
    <section class="dimension-activity-section">
      <div class="panel-header"><div><h3>${h(activity.name)}</h3><p class="muted">Activity grade: ${fmt(activity.activityScore)} /5</p></div></div>
      <div class="dimension-criteria-list">${activity.criteria.map((criterion) => `
        <article class="dimension-criterion">
          <div class="dimension-criterion-heading"><div><strong>${h(criterion.name)}</strong><p class="muted">${h(criterion.explanation)}</p></div><div class="criterion-math"><span>Weight ${fmt(criterion.weight * 100)}%</span><strong>${criterion.criterionAverage == null ? "—" : `${fmt(criterion.criterionAverage)} /5`}</strong><small>Contribution ${fmt(criterion.weightedContribution)} /1</small></div></div>
          <div class="criterion-evaluators">${criterion.evaluators.length ? criterion.evaluators.map((evaluator) => `<div class="criterion-evaluator ${evaluator.grade == null ? "missing" : ""}"><div><strong>${h(evaluator.evaluatorName)}</strong><span class="role-badge ${h(evaluator.evaluatorRole)}">${h(evaluator.evaluatorRole)}</span></div><div class="criterion-evaluator-actions"><div class="criterion-grade"><strong>${evaluator.grade == null ? "Missing" : `${fmt(evaluator.grade)} /5`}</strong>${evaluator.rawValue != null ? `<small>Result: ${h(evaluator.rawValue)}${criterion.unit ? ` ${h(criterion.unit)}` : ""}</small>` : ""}<small>${h(statusLabel(evaluator.status))}</small></div>${evaluator.submissionId ? `<button type="button" class="button secondary small edit-dimension-submission" data-id="${evaluator.submissionId}">Edit evaluation</button>` : ""}</div></div>`).join("") : `<p class="subtle">No published evaluator assignment for this activity.</p>`}</div>
        </article>`).join("")}</div>
    </section>`).join("");
  openModal(`<div><p class="eyebrow">Dimension grading</p><div class="panel-header"><div><h2>${h(profile.recruit.name)} · ${h(breakdown.name)}</h2><p class="muted">Every submitted evaluator grade used to calculate this dimension.</p></div><div class="dimension-modal-score"><strong>${fmt(breakdown.score)} /1</strong><small>Rank ${breakdown.rank ?? "—"} · ${breakdown.complete ? "Complete" : "Incomplete"}</small></div></div><div class="dimension-breakdown-scroll">${activitySections}</div><div class="modal-actions"><button type="button" class="button primary" id="cancelModal">Close</button></div></div>`, { wide: true });
  $("#cancelModal").onclick = closeModal;
  breakdown.activities.forEach((activity, index) => {
    const heading = $$(".dimension-activity-section .panel-header", modal)[index];
    if (heading) heading.insertAdjacentHTML("beforeend", `<button type="button" class="button primary small dimension-admin-evaluation" data-code="${activity.code}">${profile.adminEvaluations?.[activity.code] ? "Edit official evaluation" : "Add official evaluation"}</button>`);
  });
  $$(".dimension-admin-evaluation", modal).forEach((button) => button.onclick = () => { closeModal(); showAdminEvaluationEditor(profile.recruit.id, button.dataset.code); });
  $$(".edit-dimension-submission", modal).forEach((button) => button.onclick = () => { closeModal(); showSubmissionDetail(button.dataset.id); });
}

function wireProfile(profile) {
  const header = $(".profile-header", host);
  if (header) {
    const meta = $("p.muted", header);
    if (meta) meta.textContent = `${profile.recruit.phoneNumber || "No phone number"} · ${profile.recruit.dateOfBirth ? `Date of birth: ${profile.recruit.dateOfBirth}` : "Date of birth not recorded"} · ${profile.recruit.present ? "Present" : "Absent"}`;
    const arrival = $(".profile-arrival", header);
    if (arrival) arrival.innerHTML = `<strong>Arrival time:</strong> ${h(profile.recruit.arrivalTime ? dateTimeInput(profile.recruit.arrivalTime).split("T")[1] : "Not recorded")}${profile.recruit.attendanceComment ? `<small class="profile-attendance-note">${h(profile.recruit.attendanceComment)}</small>` : ""}`;
  }
  const completion = $$(".panel h2", host).find((heading) => heading.textContent === "Completion")?.closest(".panel");
  if (completion) completion.innerHTML = `<h2>Completion</h2><p><strong>${profile.result?.missingCount || 0}</strong> missing component${profile.result?.missingCount === 1 ? "" : "s"}</p>${profile.result?.missingComponents?.length ? `<div class="member-list">${profile.result.missingComponents.map((item) => `<span class="member-chip">${h(item)}</span>`).join("")}</div>` : `<p class="success-text">All components are complete.</p>`}`;
  const form = $("#profileForm");
  wireBoundedNumberInputs(form);
  form.oninput = () => setDirty(true);
  $("#discardProfile").onclick = () => { state.dirty = false; renderProfiles(); };
  form.onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const value = (key) => data.get(key) === "" ? null : Number(data.get(key));
    try {
      await api(`/api/admin/journeys/${state.journey.id}/recruits/${profile.recruit.id}/profile`, mutation("PUT", { punctuality: value("punctuality"), respect: value("respect"), seriousness: value("seriousness"), comment: data.get("comment"), notes: data.get("notes"), base_version: profile.assessment.version }));
      state.dirty = false; toast("Recruit profile saved."); await renderProfiles();
    } catch (error) { toast(error.message, "error"); }
  };
  $$(".dimension-card", host).forEach((button) => button.onclick = () => showDimensionBreakdown(profile, button.dataset.dimension));
  $$(".profile-activity-button", host).forEach((button) => button.onclick = () => showActivityBreakdown(profile, button.dataset.activityCode));
  $$(".submission-detail", host).forEach((button) => button.onclick = () => showSubmissionDetail(button.dataset.id));
}

function auditItem(item) {
  const before = item.before && typeof item.before === "object" ? item.before : {};
  const after = item.after && typeof item.after === "object" ? item.after : {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
  const value = (input) => typeof input === "object" ? JSON.stringify(input) : String(input ?? "—");
  const changes = keys.slice(0, 8).map((key) => `<li><strong>${h(statusLabel(key))}:</strong> ${h(value(before[key]))} → ${h(value(after[key]))}</li>`).join("");
  return `<div class="audit-item detailed"><small>${localDateTime(item.createdAt)}</small><span><strong>${h(item.actorName)}</strong> · ${h(item.actorType || "admin")}<br><small>${h(item.entityType || "record")}${item.entityId ? ` · ${h(item.entityId)}` : ""}</small></span><span><strong>${h(statusLabel(item.action))}</strong>${changes ? `<ul class="audit-changes">${changes}</ul>` : ""}${item.reason ? `<small class="muted">Reason: ${h(item.reason)}</small>` : ""}</span></div>`;
  return `<div class="audit-item"><small>${localDateTime(item.createdAt)}</small><span><strong>${h(item.actorName)}</strong> · ${h(item.actorType || "admin")}</span><span>${h(statusLabel(item.action))}${item.reason ? `<br><small class="muted">Reason: ${h(item.reason)}</small>` : ""}</span></div>`;
}

async function showAdminEvaluationEditor(recruitId, activityCode) {
  try {
    const detail = await api(`/api/admin/journeys/${state.journey.id}/recruits/${recruitId}/admin-evaluations/${activityCode}`);
    const existing = detail.evaluation;
    const values = activityCode === "sport" ? (existing?.raw || {}) : (existing?.responses || {});
    openModal(`<form id="adminEvaluationForm"><p class="eyebrow">Official admin evaluation</p><h2>${h(detail.recruit.name)} · ${h(detail.activityName)}</h2>${existing ? `<div class="warning-box">This admin evaluation is the official grade. Evaluator submissions remain visible but are not averaged into the result.</div>` : `<div class="warning-box">Saving this form makes the admin evaluation the official grade for this activity.</div>`}<div class="stack evaluation-editor-scroll">${detail.rubric.criteria.map((criterion) => `<label>${h(criterion.name)}<small class="muted">${h(criterion.explanation)}</small>${evaluationCriterionEditor(criterion, values[criterion.key])}</label>`).join("")}<label>Comments<textarea name="comments">${h(existing?.comments || "")}</textarea></label><label>Required reason<textarea name="reason" required placeholder="Why is the administration entering or changing this evaluation?"></textarea></label></div><div class="modal-actions">${existing ? `<button type="button" class="button danger" id="removeAdminEvaluation">Remove admin evaluation</button>` : ""}<button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Save official evaluation</button></div></form>`, { wide: true });
    $("#cancelModal").onclick = closeModal;
    const formElement = $("#adminEvaluationForm");
    wireDurationPickers(formElement); wireBoundedNumberInputs(formElement);
    formElement.onsubmit = async event => {
      event.preventDefault(); const form = new FormData(event.currentTarget); const responses = {}, raw = {};
      for (const criterion of detail.rubric.criteria) {
        if (activityCode === "sport") raw[criterion.key] = form.get(criterion.key);
        else responses[criterion.key] = Number(form.get(criterion.key));
      }
      try {
        await api(`/api/admin/journeys/${state.journey.id}/recruits/${recruitId}/admin-evaluations/${activityCode}`, mutation("PUT", { responses, raw, comments: form.get("comments"), reason: form.get("reason"), client_version: existing?.version || null }));
        closeModal(); toast("Official admin evaluation saved."); await refreshJourney(); await renderSection();
      } catch (error) { toast(error.message, "error"); }
    };
    if ($("#removeAdminEvaluation")) $("#removeAdminEvaluation").onclick = async () => {
      const reason = prompt("Required reason for removing the admin evaluation:"); if (!reason) return;
      try { await api(`/api/admin/journeys/${state.journey.id}/recruits/${recruitId}/admin-evaluations/${activityCode}?reason=${encodeURIComponent(reason)}`, mutation("DELETE")); closeModal(); toast("Admin evaluation removed; evaluator scores are official again."); await refreshJourney(); await renderSection(); }
      catch (error) { toast(error.message, "error"); }
    };
  } catch (error) { toast(error.message, "error"); }
}

function normalizedWhatsAppPhone(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  let digits = raw.replace(/\D/g, "");
  if (raw.startsWith("+")) {
    // Already in international format.
  } else if (digits.startsWith("00")) {
    digits = digits.slice(2);
  } else if (!digits.startsWith("961") && digits.length <= 8) {
    digits = `961${digits.replace(/^0/, "")}`;
  }
  return /^\d{8,15}$/.test(digits) ? digits : null;
}

function whatsAppAccountReady(account) {
  return Boolean(account?.managedPassword && normalizedWhatsAppPhone(account.phoneNumber));
}

function accountWhatsAppUrl(account) {
  if (!whatsAppAccountReady(account)) return null;
  const message = `Username: ${account.username}\nPassword: ${account.managedPassword}`;
  return `https://wa.me/${normalizedWhatsAppPhone(account.phoneNumber)}?text=${encodeURIComponent(message)}`;
}

function openAccountWhatsApp(account) {
  const url = accountWhatsAppUrl(account);
  if (!url) return toast("A phone number and visible password are required.", "error");
  window.open(url, "_blank", "noopener,noreferrer");
  toast(`WhatsApp opened for ${account.username}. Press Send in WhatsApp to deliver it.`);
}

function presentWhatsAppAccounts(accounts) {
  const byUsername = new Map(accounts.map((account) => [normalizedName(account.username), account]));
  return state.journey.evaluators
    .filter((evaluator) => evaluator.present && evaluator.active)
    .sort(evaluatorAttendanceSort)
    .map((evaluator) => byUsername.get(normalizedName(evaluator.name)))
    .filter((account) => whatsAppAccountReady(account));
}

function openPresentWhatsAppQueue(eligible) {
  const presentCount = state.journey.evaluators.filter((item) => item.present && item.active).length;
  const unavailable = Math.max(presentCount - eligible.length, 0);
  if (!eligible.length) return toast("No present evaluator has both a phone number and visible password.", "error");
  let index = 0;
  openModal(`<div class="whatsapp-queue"><p class="eyebrow">Present evaluators</p><h2>WhatsApp credentials</h2><p>Open each chat, then press Send in WhatsApp. The app never marks a message as sent because WhatsApp must confirm delivery.</p><div id="whatsappQueueProgress" class="formula-note"></div><div id="whatsappQueueRecipient" class="directory-selection"></div>${unavailable ? `<p class="warning-box">${unavailable} present evaluator${unavailable === 1 ? " is" : "s are"} skipped because a phone number or visible password is missing.</p>` : ""}<div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Close</button><button type="button" class="button whatsapp" id="whatsappQueueNext">Open next in WhatsApp</button></div></div>`);
  $("#cancelModal").onclick = closeModal;
  const draw = () => {
    const account = eligible[index];
    $("#whatsappQueueProgress").textContent = index < eligible.length ? `${index + 1} of ${eligible.length}` : `${eligible.length} chats opened`;
    $("#whatsappQueueRecipient").innerHTML = account ? `<strong>${h(account.username)}</strong><span>${h(account.fullName || "Full name not recorded")}</span><span>${h(account.phoneNumber)}</span>` : `<strong>Queue complete</strong><span>All available chats have been opened.</span>`;
    $("#whatsappQueueNext").disabled = !account;
    $("#whatsappQueueNext").textContent = account ? `Open ${account.username}` : "Complete";
  };
  $("#whatsappQueueNext").onclick = () => {
    if (index >= eligible.length) return;
    openAccountWhatsApp(eligible[index]);
    index += 1;
    draw();
  };
  draw();
}

async function renderPermissions() {
  if (!state.isOwner) throw new Error("Owner access is required.");
  await refreshJourney();
  const [accounts, accountAudit] = await Promise.all([api("/api/auth/accounts"), api("/api/auth/account-audit")]);
  const eligibleWhatsApp = presentWhatsAppAccounts(accounts);
  host.innerHTML = `${sectionHeading("Security", "Access & permissions", "", `<button class="button whatsapp" id="sharePresentCredentials" ${eligibleWhatsApp.length ? "" : "disabled"}>WhatsApp present evaluators (${eligibleWhatsApp.length})</button><button class="button secondary" id="generateAccounts">Generate missing evaluator passwords</button><button class="button primary" id="addAccount">Add account</button>`)}
    <div class="panel permissions-panel"><div class="permissions-toolbar"><label class="permissions-search-label" for="permissionsSearch">Find an account</label><input id="permissionsSearch" class="search-input" type="search" autocomplete="off" placeholder="Search username, full name, or phone"></div><div class="table-wrap"><table class="permissions-table"><colgroup><col class="permissions-user"><col class="permissions-password"><col class="permissions-role"><col class="permissions-toggle"><col class="permissions-toggle"><col class="permissions-attendance"><col class="permissions-toggle"><col class="permissions-actions-column"></colgroup><thead><tr><th>Username</th><th>Password</th><th>Role</th><th>Admin</th><th>Results</th><th class="permissions-attendance-heading">${h(state.journey.name)} attendance</th><th>Active</th><th>Actions</th></tr></thead><tbody>${accounts.map((account) => `<tr data-account-id="${account.id}" data-version="${account.version}" data-account-search="${h(normalizedName(`${account.username} ${account.fullName || ""} ${account.phoneNumber || ""}`))}"><td><div class="account-identity"><strong>${h(account.username)}</strong>${account.isOwner ? `<small class="success-text">Owner</small>` : ""}<small>${h(account.fullName || "Full name not recorded")}</small>${account.phoneNumber ? `<small>${h(account.phoneNumber)}</small>` : ""}</div></td><td>${account.isOwner ? `<span class="subtle">Owner-managed</span>` : account.managedPassword ? `<div class="managed-password"><input class="managed-password-value" type="password" readonly value="${h(account.managedPassword)}" aria-label="${h(account.username)} password"><button type="button" class="button ghost small reveal-password">Show</button><button type="button" class="button ghost small copy-password">Copy</button></div>` : `<span class="danger-text">Generate password</span>`}</td><td><select class="account-role" ${account.isOwner ? "disabled" : ""}><option value="overall" ${account.evaluatorRole === "overall" ? "selected" : ""}>Overall</option><option value="dossard" ${account.evaluatorRole === "dossard" ? "selected" : ""}>Dossard</option></select></td><td><input class="account-admin attendance-check" type="checkbox" ${account.canAdmin ? "checked" : ""} ${account.isOwner ? "disabled" : ""}></td><td><input class="account-results attendance-check" type="checkbox" ${account.canResults ? "checked" : ""} ${account.isOwner ? "disabled" : ""}></td><td><input class="account-attendance attendance-check" type="checkbox" ${account.attendanceJourneyIds.includes(state.journey.id) || account.isOwner || account.canAdmin ? "checked" : ""} ${account.isOwner || account.canAdmin ? "disabled" : ""}></td><td><input class="account-active attendance-check" type="checkbox" ${account.active ? "checked" : ""} ${account.isOwner ? "disabled" : ""}></td><td><div class="permissions-actions"><button class="button whatsapp small whatsapp-account" ${whatsAppAccountReady(account) ? "" : "disabled"} title="${h(whatsAppAccountReady(account) ? `Open WhatsApp for ${account.username}` : "A phone number and visible password are required")}">WhatsApp</button><button class="button ghost small edit-account">Edit info</button><button class="button secondary small save-account" ${account.isOwner ? "disabled" : ""}>Save access</button><button class="button ghost small reset-account">Reset password</button><button class="button danger small delete-account" ${account.isOwner ? "disabled" : ""}>Delete</button></div></td></tr>`).join("")}</tbody></table></div><p id="permissionsNoResults" class="muted hidden">No account matches this search.</p></div><div class="panel"><div class="panel-header"><h2>Account security log</h2><span class="subtle">${accountAudit.length} events</span></div><div class="audit-list">${accountAudit.map(auditItem).join("") || `<p class="muted">No account changes yet.</p>`}</div></div>`;
  $("#permissionsSearch").oninput = (event) => {
    const query = normalizedName(event.currentTarget.value);
    let visible = 0;
    $$(`.permissions-table tbody tr`, host).forEach((row) => {
      const matches = !query || row.dataset.accountSearch.includes(query);
      row.classList.toggle("hidden", !matches);
      if (matches) visible += 1;
    });
    $("#permissionsNoResults").classList.toggle("hidden", visible > 0);
  };
  $$(".whatsapp-account", host).forEach((button) => button.onclick = () => {
    const account = accounts.find((item) => item.id === button.closest("tr").dataset.accountId);
    openAccountWhatsApp(account);
  });
  $$(".edit-account", host).forEach((button) => button.onclick = () => {
    const account = accounts.find((item) => item.id === button.closest("tr").dataset.accountId);
    openAccountInfoDialog(account);
  });
  $$(".delete-account", host).forEach((button) => button.onclick = async () => {
    const account = accounts.find((item) => item.id === button.closest("tr").dataset.accountId);
    if (!confirm(`Delete ${account.username}'s account? Their login and future evaluator-directory entry will be removed. Historical Journee data will remain.`)) return;
    button.disabled = true;
    try {
      await api(`/api/auth/accounts/${account.id}`, mutation("DELETE"));
      toast(`${account.username} deleted. Historical Journee data was preserved.`);
      await renderPermissions();
    } catch (error) { toast(error.message, "error"); button.disabled = false; }
  });
  $("#sharePresentCredentials").onclick = () => openPresentWhatsAppQueue(eligibleWhatsApp);
  $$(".save-account", host).forEach((button) => button.onclick = async () => {
    const row = button.closest("tr");
    const account = accounts.find((item) => item.id === row.dataset.accountId);
    const attendanceIds = new Set(account.attendanceJourneyIds);
    if (row.querySelector(".account-attendance").checked) attendanceIds.add(state.journey.id); else attendanceIds.delete(state.journey.id);
    button.disabled = true;
    try {
      await api(`/api/auth/accounts/${account.id}`, mutation("PATCH", {
        evaluator_role: row.querySelector(".account-role").value,
        can_admin: row.querySelector(".account-admin").checked,
        can_results: row.querySelector(".account-results").checked,
        active: row.querySelector(".account-active").checked,
        attendance_journey_ids: [...attendanceIds],
        base_version: account.version,
      }));
      toast(`${account.username} permissions saved.`);
      await renderPermissions();
    } catch (error) { toast(error.message, "error"); button.disabled = false; }
  });
  $$(".reset-account", host).forEach((button) => button.onclick = async () => {
    const account = accounts.find((item) => item.id === button.closest("tr").dataset.accountId);
    const compact = account.username.normalize("NFKD").toLocaleLowerCase().replace(/[^a-z0-9]/g, "") || "lrcuser";
    const suggested = `${compact}${Math.floor(Math.random() * 900) + 100}`;
    const password = prompt(`Enter a new password for ${account.username}:`, suggested);
    if (!password) return;
    try { await api(`/api/auth/accounts/${account.id}/reset-password`, mutation("POST", { new_password: password })); toast("Password reset. The user must change it after login."); }
    catch (error) { toast(error.message, "error"); return; }
    await renderPermissions();
  });
  $$(".reveal-password", host).forEach((button) => button.onclick = () => {
    const input = $(".managed-password-value", button.closest("td"));
    input.type = input.type === "password" ? "text" : "password";
    button.textContent = input.type === "password" ? "Show" : "Hide";
  });
  $$(".copy-password", host).forEach((button) => button.onclick = async () => {
    const input = $(".managed-password-value", button.closest("td"));
    await navigator.clipboard.writeText(input.value);
    toast("Password copied.");
  });
  $("#addAccount").onclick = addAccountDialog;
  $("#generateAccounts").onclick = async () => {
    if (!confirm("Generate simple passwords for evaluators who do not have a visible managed password, and create any missing accounts?")) return;
    const button = $("#generateAccounts");
    button.disabled = true;
    const originalLabel = button.textContent;
    try {
      const created = [];
      let remaining = 1;
      while (remaining > 0) {
        const result = await api("/api/auth/accounts/generate-missing", mutation("POST"));
        created.push(...result.created);
        remaining = Number(result.remaining || 0);
        button.textContent = remaining ? `Generating passwords… ${remaining} left` : "Preparing password file…";
        if (!result.created.length && remaining > 0) throw new Error("Account generation could not make progress.");
      }
      if (!created.length) { toast("All active evaluators already have visible passwords."); return; }
      const rows = [["Username", "Password", "Role"], ...created.map((item) => [item.username, item.password, item.role])];
      const csv = rows.map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n");
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      link.download = `LRC-evaluator-passwords-${new Date().toISOString().slice(0, 10)}.csv`;
      link.click();
      URL.revokeObjectURL(link.href);
      toast(`${created.length} evaluator passwords generated. They remain visible to the owner.`);
      await renderPermissions();
    } catch (error) { toast(error.message, "error"); }
    finally { button.disabled = false; button.textContent = originalLabel; }
  };
}

function addAccountDialog() {
  openModal(`<form id="accountForm"><h2>Add account</h2><div class="stack"><label>Username / nickname<input name="username" required maxlength="200"></label><label>Full name (optional)<input name="fullName" maxlength="200"></label><label>Phone number (optional)<input name="phoneNumber" inputmode="tel" maxlength="40"></label><label>Temporary password<input name="password" type="password" minlength="8" required></label><label>Evaluator role<select name="role"><option value="overall">Overall</option><option value="dossard">Dossard</option></select></label></div><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Add account</button></div></form>`);
  $("#cancelModal").onclick = closeModal;
  $("#accountForm").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api("/api/auth/accounts", mutation("POST", { username: form.get("username"), full_name: form.get("fullName") || null, phone_number: form.get("phoneNumber") || null, password: form.get("password"), evaluator_role: form.get("role") }));
      closeModal(); toast("Account added."); await renderPermissions();
    } catch (error) { toast(error.message, "error"); }
  };
}

function openAccountInfoDialog(account) {
  openModal(`<form id="accountInfoForm"><p class="eyebrow">Account details</p><h2>Edit ${h(account.username)}</h2><div class="stack"><label>Username / nickname<input name="username" value="${h(account.username)}" required maxlength="200" ${account.isOwner ? "readonly" : ""}></label><label>Full name (optional)<input name="fullName" value="${h(account.fullName || "")}" maxlength="200"></label><label>Phone number (optional)<input name="phoneNumber" value="${h(account.phoneNumber || "")}" inputmode="tel" maxlength="40"></label><label>Evaluator role<select name="role" ${account.isOwner ? "disabled" : ""}><option value="overall" ${account.evaluatorRole === "overall" ? "selected" : ""}>Overall</option><option value="dossard" ${account.evaluatorRole === "dossard" ? "selected" : ""}>Dossard</option></select></label></div><div class="modal-actions"><button type="button" class="button ghost" id="cancelModal">Cancel</button><button class="button primary">Save account info</button></div></form>`);
  $("#cancelModal").onclick = closeModal;
  $("#accountInfoForm").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = {
      username: form.get("username"),
      full_name: form.get("fullName") || null,
      phone_number: form.get("phoneNumber") || null,
      evaluator_role: account.isOwner ? "dossard" : form.get("role"),
      base_version: account.version,
    };
    const saveButton = event.currentTarget.querySelector("button.primary");
    saveButton.disabled = true;
    try {
      await api(`/api/auth/accounts/${account.id}`, mutation("PATCH", body));
      closeModal();
      toast("Account information saved.");
      await loadAccountUsernames();
      await renderPermissions();
    } catch (error) { toast(error.message, "error"); saveButton.disabled = false; }
  };
}

function protectionPanel(protection) {
  const status = protection.status || "inactive";
  if (!protection.configured) {
    return `<div class="panel protection-panel critical"><h2>Event-day protection unavailable</h2><p>The private GitHub control credential is not configured on this server. Contact the development team before the Journee.</p></div>`;
  }
  if (status === "stopped" && state.journey?.status === "completed") {
    return `<div class="panel protection-panel"><div class="panel-header"><div><h2>Journee completed</h2><p class="muted">Protection is stopped, open activities are closed, and evaluator edits are locked.</p></div><span class="status-pill completed">Completed</span></div><div class="inline-actions"><a class="button primary" href="/api/admin/journeys/${state.journey.id}/export.xlsx">Download management report</a><button class="button ghost" id="checkProtection">Check status</button></div></div>`;
  }
  if (status === "inactive" || status === "stopped") {
    return `<div class="panel protection-panel"><div class="panel-header"><div><h2>Event-day protection</h2><p class="muted">One action starts two independent external monitors, activates this Journee, and keeps checking the application and database.</p></div><span class="status-pill ${status}">${h(statusLabel(status))}</span></div>
      <div class="protection-start"><label>Journee duration<select id="protectionDuration"><option value="6">6 hours</option><option value="12">12 hours</option></select></label><button class="button primary" id="startProtection">Start Journee protection</button></div>
      <p class="subtle">Start this after setting up the IT desk. No GitHub page or manual monitor setup is required.</p></div>`;
  }
  const active = ["starting", "active"].includes(status);
  const remaining = Math.max(0, Number(protection.remainingSeconds || 0));
  const hours = Math.floor(remaining / 3600);
  const minutes = Math.ceil((remaining % 3600) / 60);
  const remainingText = `${hours}h ${minutes}m remaining`;
  return `<div class="panel protection-panel ${status === "incident" ? "critical" : ""}"><div class="panel-header"><div><h2>Event-day protection</h2><p class="muted">${active ? `Protection is running · ${h(remainingText)}` : status === "incident" ? "The monitoring workflow needs attention." : "The selected protection window has ended."}</p></div><span class="status-pill ${status}">${h(statusLabel(status))}</span></div>
    <div class="protection-metrics"><div><small>Duration</small><strong>${protection.durationHours} hours</strong></div><div><small>Started</small><strong>${h(localDateTime(protection.startedAt))}</strong></div><div><small>Protected until</small><strong>${h(localDateTime(protection.endsAt))}</strong></div></div>
    ${protection.error ? `<div class="warning-box critical"><strong>Incident:</strong> ${h(protection.error)}</div>` : ""}
    <div class="inline-actions"><button class="button secondary" id="checkProtection">Check now</button>${protection.runUrl ? `<a class="button ghost" href="${h(protection.runUrl)}" target="_blank" rel="noopener">Technical monitor</a>` : ""}${status === "incident" ? `<button class="button primary" id="restartProtection">Restart protection</button>` : ""}<button class="button danger" id="endJournee">End Journee</button></div>
    <p class="subtle">Ending the Journee stops the monitors, closes every open activity, locks evaluator edits, and marks this Journee completed.</p></div>`;
}

async function refreshProtectionPanel() {
  if (!state.journey || state.section !== "settings") return;
  state.lastProtectionPoll = Date.now();
  const protection = await api(`/api/admin/journeys/${state.journey.id}/event-day-protection`);
  const panel = $("#protectionPanel");
  if (!panel) return;
  panel.innerHTML = protectionPanel(protection);
  bindProtectionControls(protection);
}

function bindProtectionControls(protection) {
  if ($("#startProtection")) $("#startProtection").onclick = async () => {
    const duration = Number($("#protectionDuration").value);
    const button = $("#startProtection");
    button.disabled = true;
    button.textContent = "Starting protection…";
    try {
      await api(`/api/admin/journeys/${state.journey.id}/event-day-protection/start`, mutation("POST", { duration_hours: duration }));
      toast(`${duration}-hour Journee protection started.`);
      await refreshProtectionPanel();
    } catch (error) { toast(error.message, "error"); button.disabled = false; button.textContent = "Start Journee protection"; }
  };
  if ($("#checkProtection")) $("#checkProtection").onclick = async () => {
    toast("Checking application, database, and monitors…");
    await refreshProtectionPanel();
  };
  if ($("#restartProtection")) $("#restartProtection").onclick = async () => {
    const button = $("#restartProtection");
    button.disabled = true;
    button.textContent = "Restarting…";
    try {
      await api(`/api/admin/journeys/${state.journey.id}/event-day-protection/restart`, mutation("POST", { reason: "Restarted from incident control." }));
      toast("Replacement protection monitors started.");
      await refreshProtectionPanel();
    } catch (error) { toast(error.message, "error"); button.disabled = false; button.textContent = "Restart protection"; }
  };
  if ($("#endJournee")) $("#endJournee").onclick = async () => {
    if (!confirm("End this Journee now? This stops protection, closes open activities, locks evaluator edits, and marks the Journee completed.")) return;
    const button = $("#endJournee");
    button.disabled = true;
    button.textContent = "Ending Journee…";
    try {
      await api(`/api/admin/journeys/${state.journey.id}/event-day-protection/end`, mutation("POST", { reason: "Journee ended from event-day control." }));
      toast("Journee completed and protection stopped.");
      await refreshJourney();
      await renderSection();
    } catch (error) { toast(error.message, "error"); button.disabled = false; button.textContent = "End Journee"; }
  };
}

async function renderSettings() {
  await refreshJourney();
  const [auditEvents, protection] = await Promise.all([
    api(`/api/admin/journeys/${state.journey.id}/audit?limit=500`),
    api(`/api/admin/journeys/${state.journey.id}/event-day-protection`),
  ]);
  state.lastProtectionPoll = Date.now();
  const evalUrl = `${location.origin}/evaluate`;
  const viewUrl = `${location.origin}/view`;
  const attendanceUrl = state.journey.recruitAttendancePath ? `${location.origin}${state.journey.recruitAttendancePath}` : "";
  host.innerHTML = `${sectionHeading("Configuration", "Settings, audit & export", "Manage event metadata, evaluator access, archives, and complete data extracts.")}
    <div class="two-column"><div class="panel"><h2>Journee metadata</h2><form id="settingsForm" class="stack"><label>Name<input name="name" value="${h(state.journey.name)}" required></label><label>Date<input name="date" type="date" value="${h(state.journey.eventDate)}" required></label><label>Status<select name="status">${["draft", "ready", "active", "completed", "archived"].map((value) => `<option value="${value}" ${value === state.journey.status ? "selected" : ""}>${h(statusLabel(value))}</option>`).join("")}</select></label><button class="button primary">Save settings</button></form></div>
    <div class="panel"><h2>Access links & QR codes</h2><div class="access-link-block"><h3>Evaluator link</h3><p class="muted">This permanent link automatically opens the single Journee whose status is Active.</p><input readonly value="${h(evalUrl)}" id="evalLink"><div class="inline-actions" style="margin:10px 0"><button class="button secondary" id="copyLink">Copy evaluator link</button></div><img src="/api/admin/journeys/${state.journey.id}/evaluator-qr.png" alt="Permanent evaluator link QR code"></div><div class="access-link-block"><h3>Recruit attendance link</h3><p class="muted">Share only with the person recording recruit attendance. It permits recruit roster changes while this Journee is Draft, Ready, or Active, and closes automatically afterward.</p><input readonly value="${h(attendanceUrl)}" id="recruitAttendanceLink"><div class="inline-actions" style="margin:10px 0"><button class="button secondary" id="copyRecruitAttendanceLink" ${attendanceUrl ? "" : "disabled"}>Copy attendance link</button><button class="button ghost" id="rotateRecruitAttendanceLink">Rotate link</button></div>${attendanceUrl ? `<img src="/api/admin/journeys/${state.journey.id}/recruit-attendance-qr.png" alt="Recruit attendance link QR code">` : `<div class="warning-box">Attendance access is not configured for this Journee.</div>`}</div></div></div>
    <div id="protectionPanel">${protectionPanel(protection)}</div>
    <div class="panel"><div class="panel-header"><div><h2>View-only reports</h2><p class="muted">Download the interactive three-sheet management report with attendance, rankings, and complete recruit profiles.</p></div></div><div class="inline-actions"><a class="button primary" href="/api/admin/journeys/${state.journey.id}/export.xlsx">Interactive management report</a><a class="button ghost" href="/api/admin/journeys/${state.journey.id}/results.csv">Quick results CSV</a><a class="button ghost" href="/api/admin/journeys/${state.journey.id}/photos.zip">Photo ZIP</a><button class="button secondary" id="duplicateCurrent">Duplicate Journee</button></div></div>
    <div class="panel"><div class="panel-header"><h2>Audit history</h2><span class="subtle">${auditEvents.length} most recent events</span></div>${auditEvents.length ? `<div class="audit-list">${auditEvents.map(auditItem).join("")}</div>` : `<p class="muted">No audit events.</p>`}</div>
    <div class="panel"><h2 class="danger-text">Archive or delete</h2><p class="muted">Archiving preserves the Journee. Permanent deletion removes its attendance, rooms, assignments, evaluations, photos, and audit history.</p><div class="inline-actions"><button class="button ghost" id="archiveCurrent">Archive Journee</button><button class="button danger" id="deleteCurrent">Permanently delete Journee</button></div></div>`;
  const form = $("#settingsForm");
  const accessPanel = $$(".panel h2", host).find((item) => item.textContent === "Access links & QR codes")?.closest(".panel");
  if (accessPanel) accessPanel.insertAdjacentHTML("beforeend", `<div class="access-link-block"><h3>Read-only management link</h3><input readonly value="${h(viewUrl)}"><div class="inline-actions" style="margin:10px 0"><button class="button secondary" id="copyViewLink">Copy read-only link</button><a class="button ghost" href="/view" target="_blank" rel="noopener">Open</a></div></div>`);
  form.onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await api(`/api/admin/journeys/${state.journey.id}`, mutation("PATCH", { name: data.get("name"), event_date: data.get("date"), status: data.get("status"), base_version: state.journey.version }));
      toast("Journee settings saved."); await renderSettings();
    } catch (error) { toast(error.message, "error"); }
  };
  $("#copyLink").onclick = async () => { await navigator.clipboard.writeText(evalUrl); toast("Evaluator link copied."); };
  $("#copyViewLink").onclick = async () => { await navigator.clipboard.writeText(viewUrl); toast("Read-only management link copied."); };
  $("#copyRecruitAttendanceLink").onclick = async () => { await navigator.clipboard.writeText(attendanceUrl); toast("Recruit attendance link copied."); };
  $("#rotateRecruitAttendanceLink").onclick = async () => {
    if (!confirm("Rotate the recruit attendance link? The old link and any active attendance sessions will stop working immediately.")) return;
    try {
      await api(`/api/admin/journeys/${state.journey.id}/rotate-recruit-attendance-link`, mutation("POST"));
      toast("Recruit attendance link rotated.");
      await renderSettings();
    } catch (error) { toast(error.message, "error"); }
  };
  bindProtectionControls(protection);
  $("#duplicateCurrent").onclick = () => duplicateJourney(state.journey.id);
  $("#archiveCurrent").onclick = () => archiveCurrent();
  $("#deleteCurrent").onclick = async () => {
    if (!confirm(`Permanently delete ${state.journey.name} and all associated data? This cannot be undone.`)) return;
    try { await api(`/api/admin/journeys/${state.journey.id}`, mutation("DELETE")); toast("Journee permanently deleted."); state.journey = null; await loadLibrary(); } catch (error) { toast(error.message, "error"); }
  };
}

async function archiveCurrent() {
  if (!confirm("Archive this Journee?")) return;
  try {
    await api(`/api/admin/journeys/${state.journey.id}`, mutation("PATCH", { status: "archived", base_version: state.journey.version }));
    toast("Journee archived."); state.journey = null; await loadLibrary();
  } catch (error) { toast(error.message, "error"); }
}

initialize();
