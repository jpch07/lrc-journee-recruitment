import { api, escapeHtml as h, fmt } from "/static/common.js?v=20260807.4";

const host = document.querySelector("#viewerHost");
const logout = document.querySelector("#viewerLogout");
const state = { session: null, journeys: [], journeyId: "", tab: "results", data: null, profileId: "" };

async function loginScreen(message = "") {
  let names = [];
  try { names = await api("/api/auth/usernames"); } catch (_) {}
  host.innerHTML = `<section class="attendance-welcome"><p class="eyebrow">LRC Journee</p><h1>Management view</h1><p>Sign in with an account that has Results access.</p></section><form id="viewerLogin" class="attendance-unlock-card"><label>Username<input name="username" list="viewerNames" autocomplete="username" required autofocus></label><datalist id="viewerNames">${names.map(item => `<option value="${h(item.username)}"></option>`).join("")}</datalist><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button class="button primary wide">Log in</button><p class="form-error">${h(message)}</p></form>`;
  document.querySelector("#viewerLogin").onsubmit = async event => {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    try { const session = await api("/api/auth/login", { method: "POST", body: { username: values.get("username"), password: values.get("password") } }); if (!(session.isOwner || session.canAdmin || session.canResults)) throw new Error("This account does not have Results access."); state.session = session; await loadJourneys(); }
    catch (error) { loginScreen(error.message); }
  };
}

async function loadJourneys() {
  state.journeys = await api("/api/view/journeys");
  if (!state.journeyId) state.journeyId = state.journeys[0]?.id || "";
  logout.classList.remove("hidden");
  if (state.journeyId) await loadJourney(); else host.innerHTML = `<div class="loading-card">No Journees are available.</div>`;
}
async function loadJourney() { state.data = await api(`/api/view/journeys/${state.journeyId}`); await render(); }

function toolbar() { return `<div class="viewer-toolbar"><label>Journee<select id="viewerJourney">${state.journeys.map(item => `<option value="${item.id}" ${item.id === state.journeyId ? "selected" : ""}>${h(item.name)} · ${h(item.eventDate)}</option>`).join("")}</select></label><div class="tabs"><button data-tab="results" class="${state.tab === "results" ? "active" : ""}">Results</button><button data-tab="attendance" class="${state.tab === "attendance" ? "active" : ""}">Attendance</button><button data-tab="profiles" class="${state.tab === "profiles" ? "active" : ""}">Profiles</button></div></div>`; }
function resultsView() { return `<div class="panel table-wrap"><table><thead><tr><th>Rank</th><th>Recruit</th><th>Overall /20</th><th>Color</th><th>Missing</th></tr></thead><tbody>${state.data.results.rows.map(row => `<tr><td>${row.overallRank ?? "—"}</td><td><button class="link-button view-profile" data-id="${row.recruitId}">${h(row.name)}</button></td><td>${fmt(row.overallScore)}</td><td>${h(row.color)}</td><td>${row.missingCount}</td></tr>`).join("")}</tbody></table></div>`; }
function attendanceView() { return `<div class="viewer-card-grid">${state.data.recruits.map(item => `<article class="panel"><h3>${h(item.name)}</h3><p><span class="status ${item.present ? "green" : "red"}">${item.present ? "Present" : "Absent"}</span></p><p>${h(item.phoneNumber || "No phone number")}</p><p>${item.dateOfBirth ? `Date of birth: ${h(item.dateOfBirth)}` : "Date of birth not recorded"}</p><p>${item.arrivalTime ? `Arrival: ${new Date(item.arrivalTime).toLocaleString()}` : "Arrival not recorded"}</p>${item.attendanceComment ? `<p class="subtle">Attendance note: ${h(item.attendanceComment)}</p>` : ""}</article>`).join("")}</div>`; }
async function profileView() {
  if (!state.profileId) state.profileId = state.data.recruits[0]?.id || "";
  if (!state.profileId) return `<div class="panel">No recruits.</div>`;
  const profile = await api(`/api/view/journeys/${state.journeyId}/recruits/${state.profileId}/profile`); const row = profile.result || { dimensions: {}, activities: {} };
  return `<div class="panel viewer-profile-select"><label>Recruit<select id="viewerRecruit">${state.data.recruits.map(item => `<option value="${item.id}" ${item.id === state.profileId ? "selected" : ""}>${h(item.name)}</option>`).join("")}</select></label></div><div class="panel"><div class="profile-header">${profile.photoUrl ? `<button class="photo-zoom-trigger profile-photo-trigger" data-photo="${profile.photoUrl}"><img class="profile-photo" src="${profile.photoUrl}" alt="${h(profile.recruit.name)}"></button>` : `<span class="profile-photo avatar placeholder">${h(profile.recruit.name[0])}</span>`}<div><h2>${h(profile.recruit.name)}</h2><p>${profile.recruit.dateOfBirth ? `Date of birth: ${h(profile.recruit.dateOfBirth)}` : "Date of birth not recorded"}</p><p>${profile.recruit.arrivalTime ? `Arrival: ${new Date(profile.recruit.arrivalTime).toLocaleString()}` : "Arrival not recorded"}</p></div><div class="score-orb"><div><strong>${fmt(row.overallScore)}</strong><small>/20 · rank ${row.overallRank ?? "—"}</small></div></div></div></div><div class="profile-dimension-grid">${Object.values(row.dimensions || {}).map(item => `<article class="panel"><h3>${h(item.name)}</h3><strong>${fmt(item.score)} /1</strong><p>Rank ${item.rank ?? "—"}</p></article>`).join("")}</div><div class="profile-activity-grid">${Object.entries(row.activities || {}).map(([code,item]) => `<article class="panel"><h3>${h(code.replaceAll("_", " "))}</h3><strong>${fmt(item.score)} /5</strong><p>Rank ${item.rank ?? "—"}</p></article>`).join("")}</div>${profile.assessment.comment ? `<div class="panel"><h3>Comment</h3><p>${h(profile.assessment.comment)}</p></div>` : ""}`;
}
async function render() {
  host.innerHTML = toolbar() + (state.tab === "results" ? resultsView() : state.tab === "attendance" ? attendanceView() : await profileView());
  document.querySelector("#viewerJourney").onchange = async event => { state.journeyId = event.target.value; state.profileId = ""; await loadJourney(); };
  document.querySelectorAll("[data-tab]").forEach(button => button.onclick = async () => { state.tab = button.dataset.tab; await render(); });
  document.querySelectorAll(".view-profile").forEach(button => button.onclick = async () => { state.profileId = button.dataset.id; state.tab = "profiles"; await render(); });
  document.querySelector("#viewerRecruit")?.addEventListener("change", async event => { state.profileId = event.target.value; await render(); });
  document.querySelector("[data-photo]")?.addEventListener("click", event => { const dialog = document.querySelector("#photoViewer"); dialog.querySelector("img").src = event.currentTarget.dataset.photo; dialog.showModal(); });
}
logout.onclick = async () => { try { await api("/api/auth/logout", { method: "POST", headers: { "X-CSRF-Token": state.session.csrfToken } }); } catch (_) {} state.session = null; logout.classList.add("hidden"); loginScreen(); };
document.querySelector("#photoViewer .photo-viewer-close").onclick = () => document.querySelector("#photoViewer").close();
(async () => { try { state.session = await api("/api/auth/session"); if (!(state.session.isOwner || state.session.canAdmin || state.session.canResults)) throw new Error(); await loadJourneys(); } catch (_) { loginScreen(); } })();
