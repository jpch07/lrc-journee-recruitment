import { api, escapeHtml as h, selectedAccount, toast, wireAccountPicker, wireRecruitDirectoryPicker } from "/static/common.js?v=20260810.1";
import { initializeSystemUI } from "/static/system-ui.js?v=20260820.2";

const host = document.querySelector("#recruitAttendanceHost");
const journeyLabel = document.querySelector("#attendanceJourneyName");
const logoutButton = document.querySelector("#attendanceLogout");
const modal = document.querySelector("#attendanceModal");
const modalBody = document.querySelector("#attendanceModalBody");
const photoViewer = document.querySelector("#photoViewer");
const token = decodeURIComponent(location.pathname.split("/").filter(Boolean).at(-1) || "");

const state = {
  landing: null,
  session: null,
  recruits: [],
  draft: {},
  query: "",
  saves: new Map(),
  pollTimer: null,
};

function mutation(method, body) {
  return { method, headers: { "X-CSRF-Token": state.session.csrfToken }, body };
}

function dateTimeInput(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Beirut",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value)).replace(" ", "T");
}

function nowBeirutInput() {
  return dateTimeInput(new Date().toISOString());
}

function renderUnlock() {
  logoutButton.classList.add("hidden");
  host.innerHTML = `<section class="attendance-welcome"><p class="eyebrow">${h(state.landing.eventDate)}</p><h1>${h(state.landing.name)}</h1></section>
    <form id="attendanceUnlockForm" class="attendance-unlock-card"><label>Username<div class="account-search-picker"><input name="username" id="attendanceUsername" autocomplete="off" maxlength="200" placeholder="Search your name" required autofocus><div id="attendanceUsernames" class="search-suggestions account-suggestions" role="listbox"></div></div></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button type="submit" id="attendanceLoginSubmit" class="button primary wide">Log in</button><p id="attendanceUnlockError" class="form-error" role="alert"></p></form>`;
  let accounts = [];
  api("/api/auth/usernames").then(items => { accounts = items; wireAccountPicker(document.querySelector("#attendanceUsername"), document.querySelector("#attendanceUsernames"), accounts); }).catch(() => {});
  document.querySelector("#attendanceUnlockForm").onsubmit = async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const error = document.querySelector("#attendanceUnlockError");
    button.disabled = true;
    error.textContent = "";
    try {
      const values = new FormData(event.currentTarget);
      const selected = selectedAccount(accounts, values.get("username"));
      if (!selected) throw new Error("Select a username from the evaluator list.");
      const account = await api("/api/auth/login", {
        method: "POST",
        body: { username: selected.username, password: values.get("password") },
      });
      state.session = await api(`/api/public/recruit-attendance/${encodeURIComponent(token)}/select`, { method: "POST", headers: { "X-CSRF-Token": account.csrfToken } });
      logoutButton.classList.remove("hidden");
      await loadRoster();
      startAttendancePolling();
    } catch (problem) {
      error.textContent = problem.message;
      button.disabled = false;
    }
  };
}

function filteredRecruits() {
  const query = state.query.trim().toLocaleLowerCase();
  return Object.values(state.draft)
    .filter((item) => item.active && (!query || `${item.name} ${item.phoneNumber || ""}`.toLocaleLowerCase().includes(query)))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
}

function recruitCard(item) {
  const photoUrl = `/api/recruit-attendance/recruits/${item.id}/photo?v=${item.version}`;
  const photo = item.hasPhoto
    ? `<button type="button" class="photo-zoom-trigger attendance-photo" data-photo-url="${photoUrl}" data-photo-name="${h(item.name)}"><img src="${photoUrl}" alt="${h(item.name)}"></button>`
    : `<span class="attendance-photo placeholder">${h(item.name.slice(0, 1))}</span>`;
  return `<article class="attendance-recruit-card ${item.present ? "present" : ""}" data-id="${item.id}">
    <div class="attendance-recruit-heading">${photo}<div><h2>${h(item.name)}</h2><span class="attendance-state">${item.present ? "Present" : "Not present"}</span></div><label class="attendance-toggle"><input type="checkbox" class="attendance-present" ${item.present ? "checked" : ""}><span>Present</span></label></div>
    <div class="attendance-fields"><label>Phone number<input class="attendance-phone" inputmode="tel" autocomplete="tel" value="${h(item.phoneNumber || "")}" placeholder="Phone number"></label><label>Date of birth<input class="attendance-dob" type="date" value="${h(item.dateOfBirth || "")}"></label><label class="attendance-arrival-field">Time of arrival<input class="attendance-arrival" type="datetime-local" value="${h(dateTimeInput(item.arrivalTime))}" ${item.present ? "" : "disabled"}></label><label class="attendance-comment-field">Attendance comment<input class="attendance-comment" value="${h(item.attendanceComment || "")}" placeholder="Reason for tardiness (optional)"></label></div>
    <div class="attendance-card-footer"><span class="row-sync ${state.saves.has(item.id) ? "saving" : "saved"}" data-sync-id="${item.id}">${state.saves.has(item.id) ? "Saving…" : "Saved"}</span><div class="attendance-card-actions"><button type="button" class="button secondary small attendance-photo-change">${item.hasPhoto ? "Replace photo" : "+ Add photo"}</button>${item.hasPhoto ? `<button type="button" class="button ghost small attendance-photo-remove">Remove photo</button>` : ""}<button type="button" class="button danger small attendance-delete">Remove recruit</button></div></div>
  </article>`;
}

function renderRoster() {
  const rows = filteredRecruits();
  const total = Object.values(state.draft).filter((item) => item.active).length;
  const present = Object.values(state.draft).filter((item) => item.active && item.present).length;
  host.innerHTML = `<section class="attendance-summary"><div><p class="eyebrow">${h(state.landing.eventDate)}</p><h1>${h(state.landing.name)}</h1><p class="muted">Signed in as ${h(state.session.displayName)}</p></div><div class="attendance-count"><strong>${present}</strong><span>of ${total} present</span></div></section>
    <div class="attendance-tools"><input id="attendanceRosterSearch" type="search" value="${h(state.query)}" placeholder="Search recruit or phone"><button id="attendanceRefresh" class="button ghost">Refresh</button><button id="attendanceAdd" class="button secondary">+ Add recruit</button></div>
    <div id="attendanceCards" class="attendance-card-list">${rows.length ? rows.map(recruitCard).join("") : `<div class="empty-state"><p>${state.query ? "No recruits match this search." : "No recruits have been added yet."}</p></div>`}</div>
    <div class="attendance-save-bar single"><span id="attendanceSyncStatus" class="subtle">${state.saves.size ? `Saving ${state.saves.size} recruit change${state.saves.size === 1 ? "" : "s"}…` : "All changes saved · checking for updates every 5 seconds"}</span></div>`;
  wireRoster();
}

function wireRoster() {
  const search = document.querySelector("#attendanceRosterSearch");
  search.oninput = (event) => {
    state.query = event.target.value;
    renderRoster();
    const next = document.querySelector("#attendanceRosterSearch");
    next.focus();
    next.setSelectionRange(next.value.length, next.value.length);
  };
  document.querySelector("#attendanceRefresh").onclick = async () => {
    if (state.saves.size) return toast("Waiting for the current recruit changes to finish saving.");
    await loadRoster({ force: true });
  };
  document.querySelector("#attendanceAdd").onclick = openAddDialog;
  document.querySelectorAll(".attendance-recruit-card").forEach((card) => {
    const item = state.draft[card.dataset.id];
    card.querySelector(".attendance-present").onchange = (event) => {
      item.present = event.target.checked;
      const arrival = card.querySelector(".attendance-arrival");
      arrival.disabled = !item.present;
      if (item.present && !arrival.value) arrival.value = nowBeirutInput();
      item.arrivalTime = item.present && arrival.value ? new Date(arrival.value).toISOString() : null;
      queueRecruitSave(item, { present: item.present, arrival_time: item.arrivalTime });
      renderRoster();
    };
    card.querySelector(".attendance-phone").oninput = (event) => { item.phoneNumber = event.target.value; queueRecruitSave(item, { phone_number: item.phoneNumber || null }, 550); };
    card.querySelector(".attendance-dob").onchange = (event) => { item.dateOfBirth = event.target.value || null; queueRecruitSave(item, { date_of_birth: item.dateOfBirth }); };
    card.querySelector(".attendance-arrival").onchange = (event) => { item.arrivalTime = event.target.value ? new Date(event.target.value).toISOString() : null; queueRecruitSave(item, { arrival_time: item.arrivalTime }); };
    card.querySelector(".attendance-comment").oninput = (event) => { item.attendanceComment = event.target.value; queueRecruitSave(item, { attendance_comment: item.attendanceComment }, 550); };
    card.querySelector(".attendance-photo-change").onclick = () => openPhotoDialog(item);
    card.querySelector(".attendance-photo-remove")?.addEventListener("click", () => removePhoto(item));
    card.querySelector(".attendance-delete").onclick = () => removeRecruit(item);
  });
  document.querySelectorAll(".attendance-photo[data-photo-url]").forEach((button) => {
    button.onclick = () => {
      document.querySelector("#photoViewerImage").src = button.dataset.photoUrl;
      document.querySelector("#photoViewerImage").alt = button.dataset.photoName;
      document.querySelector("#photoViewerCaption").textContent = button.dataset.photoName;
      photoViewer.showModal();
    };
  });
}

function attendanceSyncLabel(recruitId, text, kind = "saving") {
  const rowStatus = document.querySelector(`[data-sync-id="${recruitId}"]`);
  if (rowStatus) {
    rowStatus.textContent = text;
    rowStatus.className = `row-sync ${kind}`;
  }
  const summary = document.querySelector("#attendanceSyncStatus");
  if (summary) summary.textContent = state.saves.size
    ? `Saving ${state.saves.size} recruit change${state.saves.size === 1 ? "" : "s"}…`
    : "All changes saved · checking for updates every 5 seconds";
}

function queueRecruitSave(item, changes, delay = 0) {
  let entry = state.saves.get(item.id);
  if (!entry) {
    entry = { changes: {}, timer: null, inFlight: false, retries: 0 };
    state.saves.set(item.id, entry);
  }
  Object.assign(entry.changes, changes);
  entry.retries = 0;
  clearTimeout(entry.timer);
  attendanceSyncLabel(item.id, delay ? "Typing…" : "Saving…");
  entry.timer = setTimeout(() => runRecruitSave(item.id), delay);
}

function applyPendingChanges(item, changes) {
  if ("present" in changes) item.present = changes.present;
  if ("arrival_time" in changes) item.arrivalTime = changes.arrival_time;
  if ("phone_number" in changes) item.phoneNumber = changes.phone_number || "";
  if ("date_of_birth" in changes) item.dateOfBirth = changes.date_of_birth;
  if ("attendance_comment" in changes) item.attendanceComment = changes.attendance_comment || "";
}

async function runRecruitSave(recruitId) {
  const entry = state.saves.get(recruitId);
  const item = state.draft[recruitId];
  if (!entry || !item || entry.inFlight || !Object.keys(entry.changes).length) return;
  entry.inFlight = true;
  const sent = entry.changes;
  entry.changes = {};
  attendanceSyncLabel(recruitId, "Saving…");
  try {
    const saved = await api(`/api/recruit-attendance/recruits/${recruitId}`, mutation("PATCH", { base_version: item.version, ...sent }));
    item.version = saved.version;
    const stored = state.recruits.find((value) => value.id === recruitId);
    if (stored) stored.version = saved.version;
    entry.retries = 0;
    attendanceSyncLabel(recruitId, "Saved", "saved");
  } catch (problem) {
    entry.changes = { ...sent, ...entry.changes };
    if (problem.status === 409 && entry.retries < 3) {
      entry.retries += 1;
      try {
        const latest = await api("/api/recruit-attendance/recruits");
        const serverItem = latest.recruits.find((value) => value.id === recruitId && value.active);
        if (!serverItem) throw new Error("This recruit was removed on another device.");
        const pending = { ...entry.changes };
        Object.assign(item, serverItem);
        applyPendingChanges(item, pending);
        state.recruits = latest.recruits;
        attendanceSyncLabel(recruitId, "Syncing…");
      } catch (refreshError) {
        entry.retries = 3;
        toast(refreshError.message, "error");
      }
    } else {
      entry.retries += 1;
      attendanceSyncLabel(recruitId, "Retrying…", "error");
      if (entry.retries === 1) toast(`${item.name}: ${problem.message}`, "error");
    }
  } finally {
    entry.inFlight = false;
    if (Object.keys(entry.changes).length && entry.retries < 3) {
      clearTimeout(entry.timer);
      entry.timer = setTimeout(() => runRecruitSave(recruitId), entry.retries ? 350 : 0);
    } else if (!Object.keys(entry.changes).length) {
      state.saves.delete(recruitId);
      attendanceSyncLabel(recruitId, "Saved", "saved");
    } else {
      attendanceSyncLabel(recruitId, "Needs attention", "error");
    }
  }
}

function startAttendancePolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(() => {
    if (!document.hidden && state.session) loadRoster({ background: true });
  }, 5000);
}

async function loadRoster({ background = false, force = false } = {}) {
  if (background && (state.saves.size || document.activeElement?.closest?.(".attendance-recruit-card input"))) return;
  if (force) document.activeElement?.blur?.();
  try {
    const data = await api("/api/recruit-attendance/recruits");
    const oldSignature = state.recruits.map((item) => `${item.id}:${item.version}:${item.active}`).sort().join("|");
    const newSignature = data.recruits.map((item) => `${item.id}:${item.version}:${item.active}`).sort().join("|");
    state.recruits = data.recruits;
    state.draft = Object.fromEntries(data.recruits.map((item) => [item.id, structuredClone(item)]));
    if (!background) state.query = "";
    journeyLabel.textContent = data.journey.name;
    if (!background || oldSignature !== newSignature) {
      renderRoster();
      if (background && oldSignature) toast("Recruit attendance updated from another device.");
    } else if (document.querySelector("#attendanceSyncStatus")) {
      document.querySelector("#attendanceSyncStatus").textContent = "All changes saved · synced just now";
    }
  } catch (problem) {
    if (problem.status === 401) {
      clearInterval(state.pollTimer);
      state.session = null;
      renderUnlock();
    } else {
      host.innerHTML = `<div class="loading-card"><h2>Could not load recruit attendance</h2><p class="danger-text">${h(problem.message)}</p></div>`;
    }
  }
}

async function openAddDialog() {
  modalBody.innerHTML = `<div class="directory-dialog"><p class="eyebrow">Recruit roster</p><h2>Add recruit</h2><div class="loading-card">Loading the master recruit listâ€¦</div></div>`;
  modal.className = "modal";
  modal.showModal();
  try {
    const directory = await api("/api/recruit-attendance/directory");
    let selected = null;
    modalBody.innerHTML = `<form id="attendanceAddForm" class="directory-dialog stack"><p class="eyebrow">Recruit roster</p><h2>Add recruit</h2><label>Search the master recruit list<div class="account-search-picker"><input id="attendanceDirectorySearch" autocomplete="off" placeholder="Type a recruit name and press Enter" required autofocus><div id="attendanceDirectorySuggestions" class="search-suggestions directory-suggestions" role="listbox"></div></div></label><div id="attendanceDirectorySelection" class="directory-selection empty">Select a recruit to copy their phone number and date of birth.</div><p class="muted compact-note">Only administrators can add someone who is not in the master recruit list.</p><div class="modal-actions"><button type="button" class="button ghost" id="attendanceModalCancel">Cancel</button><button class="button primary" disabled>Add selected recruit</button></div></form>`;
    const button = document.querySelector("#attendanceAddForm button.primary");
    const selection = document.querySelector("#attendanceDirectorySelection");
    wireRecruitDirectoryPicker(document.querySelector("#attendanceDirectorySearch"), document.querySelector("#attendanceDirectorySuggestions"), directory.items, { onSelect: (item) => {
      selected = item;
      button.disabled = false;
      selection.classList.remove("empty");
      selection.innerHTML = `<strong>${h(item.name)}</strong><span>${h(item.phoneNumber || "Phone not recorded")}</span><span>${h(item.dateOfBirthSource || item.dateOfBirth || "Date of birth not recorded")}</span>`;
    }});
    document.querySelector("#attendanceModalCancel").onclick = () => modal.close();
    document.querySelector("#attendanceAddForm").onsubmit = async (event) => {
      event.preventDefault();
      if (!selected) return toast("Select a recruit from the master list.", "error");
      button.disabled = true;
      try {
        await api("/api/recruit-attendance/recruits/from-directory", mutation("POST", { directory_id: selected.id }));
        modal.close();
        toast(`${selected.name} added with their saved details.`);
        await loadRoster();
      } catch (problem) {
        toast(problem.message, "error");
        button.disabled = false;
      }
    };
    if (directory.stale) toast("Using the last saved recruit list because Google Sheets is temporarily unavailable.", "error");
  } catch (problem) {
    modalBody.innerHTML = `<div class="directory-dialog"><h2>Master list unavailable</h2><p class="danger-text">${h(problem.message)}</p><p class="muted">Ask an administrator to add the recruit if they are not in the list.</p><div class="modal-actions"><button type="button" class="button ghost" id="attendanceModalCancel">Close</button></div></div>`;
    document.querySelector("#attendanceModalCancel").onclick = () => modal.close();
  }
}

function applyRecruitSnapshot(saved) {
  state.draft[saved.id] = structuredClone(saved);
  const index = state.recruits.findIndex((item) => item.id === saved.id);
  if (index >= 0) state.recruits[index] = structuredClone(saved);
}

function openPhotoDialog(item) {
  if (state.saves.size) return toast("Wait for the current recruit changes to finish saving.", "error");
  modalBody.innerHTML = `<form id="attendancePhotoForm"><p class="eyebrow">Recruit photo</p><h2>${item.hasPhoto ? "Replace" : "Add"} photo for ${h(item.name)}</h2><p class="muted">Choose a photo or take one with this device. It will be resized and stored privately.</p><label>Photo<input id="attendancePhotoInput" name="photo" type="file" accept="image/*" required></label><img id="attendancePhotoPreview" class="attendance-photo-preview hidden" alt="Selected photo preview"><div class="modal-actions"><button type="button" class="button ghost" id="attendanceModalCancel">Cancel</button><button class="button primary">${item.hasPhoto ? "Replace photo" : "Upload photo"}</button></div></form>`;
  modal.className = "modal";
  modal.showModal();
  const input = document.querySelector("#attendancePhotoInput");
  const preview = document.querySelector("#attendancePhotoPreview");
  let previewUrl = null;
  document.querySelector("#attendanceModalCancel").onclick = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    modal.close();
  };
  input.onchange = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = input.files?.[0];
    if (!file) {
      preview.classList.add("hidden");
      return;
    }
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    preview.classList.remove("hidden");
  };
  document.querySelector("#attendancePhotoForm").onsubmit = async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button.primary");
    button.disabled = true;
    try {
      const saved = await api(`/api/recruit-attendance/recruits/${item.id}/photo`, {
        method: "POST",
        headers: { "X-CSRF-Token": state.session.csrfToken },
        body: new FormData(event.currentTarget),
      });
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      modal.close();
      applyRecruitSnapshot(saved);
      renderRoster();
      toast(item.hasPhoto ? "Recruit photo replaced." : "Recruit photo added.");
    } catch (problem) {
      toast(problem.message, "error");
      button.disabled = false;
    }
  };
}

async function removePhoto(item) {
  if (state.saves.size) return toast("Wait for the current recruit changes to finish saving.", "error");
  if (!confirm(`Remove the photo for ${item.name}? You can upload another photo afterward.`)) return;
  try {
    const saved = await api(`/api/recruit-attendance/recruits/${item.id}/photo`, mutation("DELETE"));
    applyRecruitSnapshot(saved);
    renderRoster();
    toast("Recruit photo removed.");
  } catch (problem) {
    toast(problem.message, "error");
  }
}

async function removeRecruit(item) {
  if (state.saves.size) return toast("Wait for the current recruit changes to finish saving.", "error");
  if (!confirm(`Remove ${item.name} from this Journee? Existing historical records will be preserved.`)) return;
  try {
    const result = await api(`/api/recruit-attendance/recruits/${item.id}`, mutation("DELETE"));
    toast(result.disposition === "deleted" ? "Recruit removed." : "Recruit deactivated; historical records were preserved.");
    await loadRoster();
  } catch (problem) {
    toast(problem.message, "error");
  }
}

logoutButton.onclick = async () => {
  if (state.saves.size) return toast("Wait for the current recruit changes to finish saving.", "error");
  try { await api("/api/auth/logout", mutation("POST")); } catch (_) { /* Session may already be expired. */ }
  clearInterval(state.pollTimer);
  state.session = null;
  renderUnlock();
};

document.querySelector("#photoViewer .photo-viewer-close").onclick = () => photoViewer.close();
photoViewer.onclick = (event) => { if (event.target === photoViewer) photoViewer.close(); };
window.addEventListener("beforeunload", (event) => {
  if (!state.saves.size) return;
  event.preventDefault();
  event.returnValue = "";
});

async function initialize() {
  try {
    state.landing = await api(`/api/public/recruit-attendance/${encodeURIComponent(token)}`);
    journeyLabel.textContent = state.landing.name;
    try {
      const account = await api("/api/auth/session");
      const session = await api(`/api/public/recruit-attendance/${encodeURIComponent(token)}/select`, { method: "POST", headers: { "X-CSRF-Token": account.csrfToken } });
      if (session.journeyId === state.landing.journeyId) {
        state.session = session;
        logoutButton.classList.remove("hidden");
        await loadRoster();
        startAttendancePolling();
        return;
      }
    } catch (_) { /* A new or expired session starts at the name screen. */ }
    renderUnlock();
  } catch (problem) {
    host.innerHTML = `<div class="loading-card"><h2>Recruit attendance unavailable</h2><p class="danger-text">${h(problem.message)}</p></div>`;
  }
}

initializeSystemUI().catch(() => {}).finally(initialize);
