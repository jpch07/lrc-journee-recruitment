import { api, escapeHtml } from "/static/common.js?v=20260810.1";

const $ = (selector) => document.querySelector(selector);
let platformSession = null;

function showAuth(mode = "login") {
  $("#authScreen").classList.remove("hidden");
  $("#workspaceScreen").classList.add("hidden");
  switchAuth(mode);
}

function switchAuth(mode) {
  const login = mode === "login";
  $("#loginTab").classList.toggle("active", login);
  $("#signupTab").classList.toggle("active", !login);
  $("#loginTab").setAttribute("aria-selected", String(login));
  $("#signupTab").setAttribute("aria-selected", String(!login));
  $("#platformLoginForm").classList.toggle("hidden", !login);
  $("#platformSignupForm").classList.toggle("hidden", login);
  $(".platform-subtitle").textContent = login
    ? "Sign in to manage your evaluation workspaces."
    : "Create one account for all the workspaces you own.";
}

function workspaceCard(item) {
  const activity = item.activeJourneyCount
    ? `${item.activeJourneyCount} active session${item.activeJourneyCount === 1 ? "" : "s"}`
    : "No active session";
  return `<article class="platform-workspace-card"><button class="platform-workspace-main" type="button" data-workspace-id="${escapeHtml(item.id)}">
    <span class="platform-workspace-icon" aria-hidden="true">${escapeHtml(item.name.slice(0, 1).toUpperCase())}</span>
    <span class="platform-workspace-info"><strong>${escapeHtml(item.name)}</strong><small>${item.journeyCount} session${item.journeyCount === 1 ? "" : "s"} · ${escapeHtml(activity)}</small></span>
    <span class="platform-workspace-open">Open <b aria-hidden="true">→</b></span>
  </button><button class="platform-workspace-delete" type="button" data-delete-workspace="${escapeHtml(item.id)}" data-workspace-name="${escapeHtml(item.name)}" aria-label="Delete ${escapeHtml(item.name)}">Delete</button></article>`;
}

async function loadWorkspaces() {
  const items = await api("/api/platform/workspaces");
  $("#workspaceList").innerHTML = items.map(workspaceCard).join("");
  $("#workspaceList").classList.toggle("hidden", !items.length);
  $("#emptyWorkspaces").classList.toggle("hidden", Boolean(items.length));
  $("#workspaceList").querySelectorAll("[data-workspace-id]").forEach((button) => {
    button.onclick = () => openWorkspace(button.dataset.workspaceId, button);
  });
  $("#workspaceList").querySelectorAll("[data-delete-workspace]").forEach((button) => {
    button.onclick = () => deleteWorkspace(button.dataset.deleteWorkspace, button.dataset.workspaceName, button);
  });
}

async function showWorkspaces(session) {
  platformSession = session;
  $("#ownerUsername").textContent = session.username;
  $("#authScreen").classList.add("hidden");
  $("#workspaceScreen").classList.remove("hidden");
  await loadWorkspaces();
}

async function openWorkspace(id, button) {
  button.disabled = true;
  try {
    const selected = await api(`/api/platform/workspaces/${encodeURIComponent(id)}/select`, {
      method: "POST",
      headers: { "X-CSRF-Token": platformSession.csrfToken },
    });
    window.location.assign(`/${encodeURIComponent(selected.slug)}/admin`);
  } catch (problem) {
    button.disabled = false;
    window.alert(problem.message);
  }
}

async function deleteWorkspace(id, name, button) {
  const confirmationName = window.prompt(
    `This permanently deletes "${name}" and every session and record inside it.\n\nType the exact workspace name to continue:`,
  );
  if (confirmationName === null) return;
  if (confirmationName !== name) {
    window.alert("The workspace name did not match. Nothing was deleted.");
    return;
  }
  button.disabled = true;
  try {
    await api(`/api/platform/workspaces/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: { "X-CSRF-Token": platformSession.csrfToken },
      body: { confirmation_name: confirmationName },
    });
    await loadWorkspaces();
  } catch (problem) {
    button.disabled = false;
    window.alert(problem.message);
  }
}

async function submitCredentials(form, endpoint) {
  const error = form.querySelector(".platform-form-error");
  const submit = form.querySelector('button[type="submit"]');
  error.textContent = "";
  submit.disabled = true;
  try {
    const session = await api(endpoint, { method: "POST", body: Object.fromEntries(new FormData(form)) });
    form.reset();
    await showWorkspaces(session);
  } catch (problem) {
    error.textContent = problem.message;
  } finally {
    submit.disabled = false;
  }
}

$("#loginTab").onclick = () => switchAuth("login");
$("#signupTab").onclick = () => switchAuth("signup");
$("#platformLoginForm").onsubmit = (event) => { event.preventDefault(); submitCredentials(event.currentTarget, "/api/platform/login"); };
$("#platformSignupForm").onsubmit = (event) => { event.preventDefault(); submitCredentials(event.currentTarget, "/api/platform/register"); };

function toggleCreate(show) {
  $("#createWorkspaceForm").classList.toggle("hidden", !show);
  $("#showCreateWorkspace").classList.toggle("hidden", show);
  if (show) $("#workspaceName").focus();
}

$("#showCreateWorkspace").onclick = () => toggleCreate(true);
$("#emptyWorkspaces button").onclick = () => toggleCreate(true);
$("#cancelCreateWorkspace").onclick = () => toggleCreate(false);
$("#createWorkspaceForm").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const error = form.querySelector(".platform-form-error");
  error.textContent = "";
  try {
    await api("/api/platform/workspaces", {
      method: "POST",
      headers: { "X-CSRF-Token": platformSession.csrfToken },
      body: Object.fromEntries(new FormData(form)),
    });
    form.reset();
    toggleCreate(false);
    await loadWorkspaces();
  } catch (problem) { error.textContent = problem.message; }
};

$("#platformLogout").onclick = async () => {
  try {
    await api("/api/platform/logout", { method: "POST", headers: { "X-CSRF-Token": platformSession.csrfToken } });
  } finally {
    platformSession = null;
    showAuth("login");
  }
};

api("/api/platform/session").then(showWorkspaces).catch(() => showAuth("login"));
