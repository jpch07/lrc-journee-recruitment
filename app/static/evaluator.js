import { api, escapeHtml as h, fmt, statusLabel, toast, uid } from "/static/common.js";

const $ = (selector, root = document) => root.querySelector(selector);
const host = $("#evalHost");
const photoViewer = $("#photoViewer");
const fixedLink = location.pathname.replace(/\/+$/, "") === "/evaluate";
const token = location.pathname.split("/").filter(Boolean).pop();
const publicBase = fixedLink ? "/api/public/current" : `/api/public/journeys/${encodeURIComponent(token)}`;
const state = {
  csrf: "",
  landing: null,
  home: null,
  view: "loading",
  activeTask: null,
  activeActivity: null,
  debounce: null,
  draftSaving: false,
  pendingDraft: false,
  poll: null,
  lastUpdateSignature: "",
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

function mutation(method, body, extra = {}) {
  return { method, body, headers: { "X-CSRF-Token": state.csrf, ...extra } };
}

async function initialize() {
  try {
    const session = await api("/api/evaluator/session");
    state.csrf = session.csrfToken;
    $("#journeyName").textContent = session.journeyName;
    $("#evalLogout").classList.remove("hidden");
    await loadHome();
  } catch {
    await showLanding();
  }
}

async function showLanding() {
  clearInterval(state.poll);
  state.view = "landing";
  $("#evalLogout").classList.add("hidden");
  try {
    state.landing = await api(publicBase);
    $("#journeyName").textContent = state.landing.name;
    host.innerHTML = `<section class="eval-welcome"><p class="eyebrow">Evaluator access</p><h1>${h(state.landing.name)}</h1><p>${h(state.landing.eventDate)}</p><p style="margin-bottom:0">Select your name to see only your assigned recruits.</p></section>
      <section class="panel" style="margin-top:14px"><h2>Who are you?</h2><label>Search evaluators<input id="nameSearch" type="search" placeholder="Start typing your name" autocomplete="off"></label><div id="nameList" class="name-list"></div></section>`;
    renderNameList(state.landing.evaluators);
    $("#nameSearch").oninput = (event) => {
      const query = event.target.value.toLowerCase().trim();
      renderNameList(state.landing.evaluators.filter((item) => item.name.toLowerCase().includes(query)));
    };
  } catch (error) {
    host.innerHTML = `<div class="loading-card"><h2>Link unavailable</h2><p class="muted">${h(error.message)}</p></div>`;
  }
}

function renderNameList(evaluators) {
  const list = $("#nameList");
  if (!evaluators.length) {
    list.innerHTML = `<p class="muted">No matching present evaluator.</p>`;
    return;
  }
  list.innerHTML = evaluators.map((item) => `<button class="name-button" data-id="${item.id}"><span>${h(item.name)}</span><span>→</span></button>`).join("");
  list.querySelectorAll("button").forEach((button) => button.onclick = () => confirmIdentity(button.dataset.id));
}

function confirmIdentity(evaluatorId) {
  const evaluator = state.landing.evaluators.find((item) => item.id === evaluatorId);
  host.insertAdjacentHTML("beforeend", `<div id="identityConfirm" class="panel" style="position:fixed;inset:auto 14px max(14px,env(safe-area-inset-bottom));z-index:40;max-width:692px;margin:auto;box-shadow:0 20px 70px rgba(0,0,0,.2)"><h2>Continue as ${h(evaluator.name)}?</h2><p class="muted">This name determines which recruits and photos you can access.</p><div class="inline-actions"><button class="button ghost" id="cancelIdentity">Cancel</button><button class="button primary" id="acceptIdentity">Yes, continue</button></div></div>`);
  $("#cancelIdentity").onclick = () => $("#identityConfirm").remove();
  $("#acceptIdentity").onclick = async () => {
    try {
      const result = await api(`${publicBase}/session`, { method: "POST", body: { evaluator_id: evaluator.id } });
      state.csrf = result.csrfToken;
      $("#evalLogout").classList.remove("hidden");
      await loadHome();
    } catch (error) { toast(error.message, "error"); }
  };
}

async function loadHome() {
  state.home = await api("/api/evaluator/home");
  state.view = "home";
  $("#journeyName").textContent = state.home.journey.name;
  renderHome();
  startPolling();
}

function renderHome() {
  const { journey, evaluator, activities } = state.home;
  const currentName = activities.find((item) => item.code === journey.currentActivity)?.name || "Waiting for admin";
  host.innerHTML = `<section class="eval-welcome"><p class="eyebrow">${journey.currentActivity ? "Activity open" : "Journee workspace"}</p><h1>${h(currentName)}</h1><p style="margin-bottom:0">Assignments update automatically. Refresh if the admin has just published a change.</p></section>
    <div class="eval-identity"><div><strong>${h(evaluator.name)}</strong><small class="muted">${h(statusLabel(evaluator.role))} evaluator</small></div><span class="role-badge ${evaluator.role}">${h(evaluator.role)}</span><span>${evaluator.roomNumber ? `Room ${evaluator.roomNumber}` : "No room"}</span></div>
    <div class="refresh-row"><h2 style="margin:0">Your activities</h2><button class="button ghost small" id="refreshTasks">↻ Refresh</button></div>
    <div class="eval-activity-list">${activities.map(activityCard).join("")}</div>`;
  $("#refreshTasks").onclick = async () => { await loadHome(); toast("Assignments refreshed."); };
  host.querySelectorAll(".start-task").forEach((button) => button.onclick = () => openTask(button.dataset.activity, button.dataset.assignment));
}

function activityCard(activity) {
  const lifecycle = activity.status === "open" ? "Open" : activity.status === "closed" ? "Closed" : "Upcoming";
  return `<article class="eval-activity-card"><header><div><h3>${h(activity.name)}</h3><small class="muted">${activity.tasks.length ? `${activity.tasks.length} assigned recruit${activity.tasks.length === 1 ? "" : "s"}` : "No assignment yet"}</small></div><span class="status-pill ${activity.status}">${lifecycle}</span></header>${activity.tasks.length ? activity.tasks.map((task) => taskRow(activity, task)).join("") : `<div class="eval-empty">${activity.status === "open" ? "You have no task for this activity." : "Assignments will appear after publication."}</div>`}</article>`;
}

function taskRow(activity, task) {
  const buttonLabel = activity.status === "closed" ? "View locked" : task.status === "submitted" ? "Edit" : task.status === "draft" ? "Continue" : "Start";
  return `<div class="eval-task">${task.photoUrl ? `<button type="button" class="photo-zoom-trigger" data-photo-viewer data-photo-url="${task.photoUrl}" data-photo-name="${h(task.recruitName)}"><img class="avatar" src="${task.photoUrl}" alt="${h(task.recruitName)}"></button>` : `<span class="avatar placeholder">${h(task.recruitName[0])}</span>`}<div><strong>${h(task.recruitName)}</strong><small class="muted">${task.roomNumber ? `Room ${task.roomNumber} · ` : ""}${h(statusLabel(task.status))}${task.submission && ["submitted", "locked"].includes(task.submission.status) ? ` · ${fmt(task.submission.score)}/5` : ""}</small></div><button class="button ${activity.status === "open" ? "primary" : "ghost"} small start-task" data-activity="${activity.code}" data-assignment="${task.assignmentId}">${buttonLabel}</button></div>`;
}

function openTask(activityCode, assignmentId) {
  const activity = state.home.activities.find((item) => item.code === activityCode);
  const task = activity.tasks.find((item) => item.assignmentId === assignmentId);
  state.view = "form";
  state.activeActivity = activity;
  state.activeTask = task;
  const serverPayload = task.submission ? { responses: task.submission.responses || {}, raw: task.submission.raw || {}, comments: task.submission.comments || "", version: task.submission.version } : { responses: {}, raw: {}, comments: "", version: null };
  const local = readLocalDraft(assignmentId);
  const payload = local && (!task.submission || task.submission.status === "draft") ? { ...serverPayload, ...local, responses: { ...serverPayload.responses, ...local.responses }, raw: { ...serverPayload.raw, ...local.raw } } : serverPayload;
  renderForm(payload);
}

function renderForm(payload) {
  const activity = state.activeActivity;
  const task = state.activeTask;
  const rubric = activity.rubric;
  const locked = activity.status !== "open";
  host.innerHTML = `<form id="evaluationForm" class="evaluation-form">
    <section class="form-recruit">${task.photoUrl ? `<button type="button" class="photo-zoom-trigger" data-photo-viewer data-photo-url="${task.photoUrl}" data-photo-name="${h(task.recruitName)}"><img class="avatar" src="${task.photoUrl}" alt="${h(task.recruitName)}"></button>` : `<span class="avatar placeholder">${h(task.recruitName[0])}</span>`}<div><p class="eyebrow" style="color:#ffc8d4">${h(activity.name)} evaluation</p><h2>${h(task.recruitName)}</h2><small>${h(state.home.evaluator.name)}${task.roomNumber ? ` · Room ${task.roomNumber}` : ""}</small></div></section>
    <div class="panel" style="margin:0"><div class="panel-header"><span><strong id="completionCount">0/${rubric.criteria.length}</strong> criteria complete</span><span class="status-pill ${activity.status}">${h(statusLabel(activity.status))}</span></div>${locked ? `<div class="warning-box">This activity is closed. The evaluation is read-only.</div>` : ""}<p class="muted">Grade every criterion. Explanations describe the behavior being assessed.</p></div>
    ${rubric.criteria.map((criterion) => criterionField(criterion, rubric.kind, payload, locked)).join("")}
    <section class="criterion-card"><label>Comments (optional)<textarea name="comments" ${locked ? "disabled" : ""} placeholder="Activity-specific observations">${h(payload.comments || "")}</textarea></label></section>
    <p id="draftState" class="draft-state">${locked ? "Locked by admin" : "Drafts save locally and to the server."}</p>
    <div class="eval-form-actions"><button type="button" class="button ghost" id="backHome">Back</button><button type="submit" class="button primary" ${locked ? "disabled" : ""}>${task.submission && ["submitted", "locked"].includes(task.submission.status) ? `Update ${h(activity.name)} evaluation` : `Submit ${h(activity.name)} evaluation`}</button></div>
  </form>`;
  const form = $("#evaluationForm");
  form.dataset.version = payload.version ?? "";
  updateCompletion();
  if (!locked) {
    form.oninput = () => {
      updateCompletion();
      const draft = collectPayload();
      writeLocalDraft(task.assignmentId, draft);
      $("#draftState").textContent = "Saved on this device; syncing…";
      clearTimeout(state.debounce);
      state.debounce = setTimeout(queueServerDraft, 700);
    };
  }
  $("#backHome").onclick = async () => {
    clearTimeout(state.debounce);
    if (!locked) await queueServerDraft(true);
    await loadHome();
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (locked) return;
    const count = completedCount();
    if (count !== rubric.criteria.length) { toast(`Complete all ${rubric.criteria.length} criteria before submitting.`, "error"); return; }
    if (!confirm(`Submit the ${activity.name} evaluation for ${task.recruitName}? You may edit it until the admin closes the activity.`)) return;
    clearTimeout(state.debounce);
    await submitEvaluation();
  };
}

function criterionField(criterion, kind, payload, locked) {
  const value = kind === "sport" ? payload.raw?.[criterion.key] ?? "" : payload.responses?.[criterion.key] ?? "";
  const input = criterion.inputType === "integer"
    ? `<input name="${criterion.key}" type="number" min="0" step="1" inputmode="numeric" value="${h(value)}" ${locked ? "disabled" : ""} required>`
    : criterion.inputType === "duration"
      ? `<input name="${criterion.key}" type="text" inputmode="text" value="${h(value)}" placeholder="seconds, MM:SS, or 1m30s" ${locked ? "disabled" : ""} required>`
      : `<input name="${criterion.key}" type="number" min="0" max="5" step="0.1" inputmode="decimal" value="${h(value)}" placeholder="0.0 to 5.0" ${locked ? "disabled" : ""} required>`;
  return `<section class="criterion-card"><span class="dimension-tag">${h(criterion.dimension)}</span><h3>${h(criterion.name)}</h3><p>${h(criterion.explanation)}</p><label>${kind === "sport" ? criterion.inputType === "integer" ? "Result (repetitions)" : "Duration" : "Grade /5"}${input}</label></section>`;
}

function completedCount() {
  const form = $("#evaluationForm");
  return state.activeActivity.rubric.criteria.filter((criterion) => String(form.elements[criterion.key]?.value || "").trim() !== "").length;
}

function updateCompletion() {
  $("#completionCount").textContent = `${completedCount()}/${state.activeActivity.rubric.criteria.length}`;
}

function collectPayload() {
  const form = $("#evaluationForm");
  const data = new FormData(form);
  const responses = {}, raw = {};
  for (const criterion of state.activeActivity.rubric.criteria) {
    const value = String(data.get(criterion.key) || "").trim();
    if (!value) continue;
    if (state.activeActivity.rubric.kind === "sport") raw[criterion.key] = value;
    else responses[criterion.key] = Number(value);
  }
  return { responses, raw, comments: String(data.get("comments") || ""), client_version: form.dataset.version ? Number(form.dataset.version) : null, savedAt: Date.now() };
}

function storageKey(assignmentId) {
  return `lrc-journee-draft:${state.home.journey.name}:${state.home.evaluator.id}:${assignmentId}`;
}

function writeLocalDraft(assignmentId, payload) {
  localStorage.setItem(storageKey(assignmentId), JSON.stringify(payload));
}

function readLocalDraft(assignmentId) {
  try { return JSON.parse(localStorage.getItem(storageKey(assignmentId)) || "null"); } catch { return null; }
}

async function queueServerDraft(force = false) {
  if (!state.activeTask || state.activeActivity.status !== "open" || !$("#evaluationForm")) return;
  if (state.draftSaving) { state.pendingDraft = true; return; }
  state.draftSaving = true;
  const payload = collectPayload();
  try {
    const result = await api(`/api/evaluator/tasks/${state.activeTask.assignmentId}/draft`, mutation("PUT", payload));
    const form = $("#evaluationForm");
    if (form && result.submission) form.dataset.version = result.submission.version;
    if ($("#draftState")) $("#draftState").textContent = "Draft synced.";
  } catch (error) {
    if ($("#draftState")) $("#draftState").textContent = `Saved on device; server sync failed: ${error.message}`;
    if (force) toast("Draft remains saved on this device.", "error");
  } finally {
    state.draftSaving = false;
    if (state.pendingDraft) { state.pendingDraft = false; queueServerDraft(); }
  }
}

async function submitEvaluation() {
  const payload = collectPayload();
  const button = $("#evaluationForm button[type=submit]");
  button.disabled = true;
  try {
    await api(`/api/evaluator/tasks/${state.activeTask.assignmentId}/submit`, mutation("POST", payload, { "Idempotency-Key": uid() }));
    localStorage.removeItem(storageKey(state.activeTask.assignmentId));
    toast("Evaluation submitted successfully.");
    await loadHome();
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
  }
}

function startPolling() {
  clearInterval(state.poll);
  state.lastUpdateSignature = state.home ? JSON.stringify({ version: state.home.journey.version, activities: state.home.activities.map((item) => [item.code, item.version]) }) : "";
  state.poll = setInterval(async () => {
    if (document.hidden) return;
    try {
      const update = await api("/api/evaluator/updates");
      const signature = JSON.stringify({ version: update.journeyVersion, activities: Object.entries(update.activityVersions) });
      if (signature !== state.lastUpdateSignature) {
        if (state.view === "home") await loadHome();
        else if (state.view === "form") {
          const currentState = update.activityVersions[state.activeActivity.code];
          if (currentState !== state.activeActivity.version) {
            toast("The admin changed this activity. Return to tasks to refresh.", "error");
          }
        }
        state.lastUpdateSignature = signature;
      }
    } catch { /* visible Refresh and local drafts provide recovery */ }
  }, 5000);
}

$("#evalLogout").onclick = async () => {
  if (state.view === "form" && state.activeActivity?.status === "open") {
    clearTimeout(state.debounce);
    await queueServerDraft(true);
  }
  try { await api("/api/evaluator/logout", mutation("POST", {})); } catch { /* session may already be gone */ }
  state.csrf = "";
  state.home = null;
  await showLanding();
};

initialize();
