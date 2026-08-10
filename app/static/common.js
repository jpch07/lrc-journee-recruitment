export async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData) && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, { credentials: "same-origin", ...options, headers });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(payload?.detail || payload || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizedAccountName(value) {
  return String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

export function selectedAccount(accounts, value) {
  const normalized = normalizedAccountName(value);
  return accounts.find((item) => normalizedAccountName(item.username) === normalized) || null;
}

export function wireAccountPicker(input, suggestions, accounts, { onSelect } = {}) {
  const ordered = [...accounts].sort((a, b) => a.username.localeCompare(b.username, undefined, { sensitivity: "base" }));
  const matches = () => {
    const query = normalizedAccountName(input.value);
    return query ? ordered.filter((item) => normalizedAccountName(`${item.username} ${item.fullName || ""}`).includes(query)).slice(0, 8) : [];
  };
  const choose = (item) => {
    input.value = item.username;
    input.dataset.selectedUsername = item.username;
    suggestions.classList.remove("visible");
    suggestions.innerHTML = "";
    onSelect?.(item);
  };
  const draw = () => {
    delete input.dataset.selectedUsername;
    const items = matches();
    suggestions.innerHTML = items.map((item) => `<button type="button" role="option" data-username="${escapeHtml(item.username)}"><span><strong>${escapeHtml(item.username)}</strong><small>${escapeHtml(item.fullName || "Full name not recorded")}</small></span><span class="role-badge ${escapeHtml(item.role || "")}">${escapeHtml(item.role || "")}</span></button>`).join("");
    suggestions.classList.toggle("visible", Boolean(input.value.trim() && items.length));
    suggestions.querySelectorAll("button").forEach((button) => button.onclick = () => choose(
      ordered.find((item) => item.username === button.dataset.username),
    ));
  };
  input.addEventListener("input", draw);
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const first = matches()[0];
    if (!first) return;
    event.preventDefault();
    choose(first);
    input.form?.querySelector('input[type="password"]')?.focus();
  });
  input.addEventListener("focus", draw);
  input.addEventListener("blur", () => setTimeout(() => suggestions.classList.remove("visible"), 150));
}

function normalizedRecruitSearch(value) {
  return String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

export function wireRecruitDirectoryPicker(input, suggestions, recruits, { onSelect } = {}) {
  const ordered = [...recruits].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  const matches = () => {
    const query = normalizedRecruitSearch(input.value);
    if (!query) return [];
    const digits = query.replace(/\D/g, "");
    return ordered.filter((item) => {
      const searchable = normalizedRecruitSearch(`${item.name} ${item.phoneNumber || ""} ${item.dateOfBirthSource || ""}`);
      return searchable.includes(query) || (digits.length >= 3 && String(item.phoneNumber || "").replace(/\D/g, "").includes(digits));
    }).slice(0, 10);
  };
  const choose = (item) => {
    if (!item) return;
    input.value = item.name;
    input.dataset.selectedDirectoryId = item.id;
    suggestions.classList.remove("visible");
    suggestions.innerHTML = "";
    onSelect?.(item);
  };
  const draw = () => {
    delete input.dataset.selectedDirectoryId;
    const items = matches();
    suggestions.innerHTML = items.map((item) => {
      const birthDate = item.dateOfBirthSource || item.dateOfBirth || "Birth date not recorded";
      return `<button type="button" role="option" data-directory-id="${escapeHtml(item.id)}"><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.phoneNumber || "Phone not recorded")}</small></span><span class="directory-birth-date">${escapeHtml(birthDate)}</span></button>`;
    }).join("");
    suggestions.classList.toggle("visible", Boolean(input.value.trim() && items.length));
    suggestions.querySelectorAll("button").forEach((button) => {
      button.onclick = () => choose(ordered.find((item) => item.id === button.dataset.directoryId));
    });
  };
  input.addEventListener("input", draw);
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const first = matches()[0];
    if (!first) return;
    event.preventDefault();
    choose(first);
  });
  input.addEventListener("focus", draw);
  input.addEventListener("blur", () => setTimeout(() => suggestions.classList.remove("visible"), 150));
}

export function fmt(value, digits = 2) {
  return Number(value || 0).toFixed(digits);
}

export function localDateTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Beirut",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function toast(message, kind = "success") {
  const host = document.querySelector("#toasts");
  if (!host) return;
  const item = document.createElement("div");
  item.className = `toast ${kind}`;
  item.textContent = message;
  host.append(item);
  setTimeout(() => item.remove(), 4200);
}

export function uid() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export function durationSeconds(value) {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const raw = String(value).trim().toLowerCase();
  if (/^\d+(?:\.\d+)?$/.test(raw)) return Math.max(0, Math.round(Number(raw)));
  if (/^\d+(?::\d{1,2}){1,2}$/.test(raw)) {
    const parts = raw.split(":").map(Number);
    if (parts.at(-1) >= 60 || (parts.length === 3 && parts.at(-2) >= 60)) return null;
    return parts.length === 2 ? parts[0] * 60 + parts[1] : parts[0] * 3600 + parts[1] * 60 + parts[2];
  }
  const compact = raw.replace(/\s+/g, "").replace(/hours?|hrs?/g, "h").replace(/minutes?|mins?/g, "m").replace(/seconds?|secs?/g, "s");
  const match = compact.match(/^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$/);
  if (!match || !match.slice(1).some(Boolean)) return null;
  return Math.max(0, Math.round(Number(match[1] || 0) * 3600 + Number(match[2] || 0) * 60 + Number(match[3] || 0)));
}

export function durationPickerHtml(name, value, disabled = false) {
  const parsed = durationSeconds(value);
  const total = parsed ?? 0;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total % 3600 / 60);
  const seconds = total % 60;
  const optionList = (maximum, selected) => Array.from({ length: Math.max(maximum, selected) + 1 }, (_, number) => `<option value="${number}" ${number === selected ? "selected" : ""}>${String(number).padStart(2, "0")}</option>`).join("");
  const status = parsed === null ? "Not entered — choose a duration" : `${hours ? `${hours}h ` : ""}${minutes}m ${seconds}s`;
  return `<div class="duration-picker" data-duration-picker><div class="duration-selector"><label><span>Hours</span><select data-duration-hours ${disabled ? "disabled" : ""}>${optionList(9, hours)}</select></label><span>:</span><label><span>Minutes</span><select data-duration-minutes ${disabled ? "disabled" : ""}>${optionList(59, minutes)}</select></label><span>:</span><label><span>Seconds</span><select data-duration-seconds ${disabled ? "disabled" : ""}>${optionList(59, seconds)}</select></label></div><input type="hidden" name="${escapeHtml(name)}" value="${parsed === null ? "" : total}"><div class="duration-picker-footer"><small class="duration-picker-status">${status}</small>${disabled ? "" : `<span><button type="button" class="duration-zero">Use 00:00:00</button><button type="button" class="duration-clear">Clear</button></span>`}</div></div>`;
}

export function wireDurationPickers(root) {
  root.querySelectorAll("[data-duration-picker]").forEach((picker) => {
    const hidden = picker.querySelector('input[type="hidden"]');
    const hours = picker.querySelector("[data-duration-hours]");
    const minutes = picker.querySelector("[data-duration-minutes]");
    const seconds = picker.querySelector("[data-duration-seconds]");
    const status = picker.querySelector(".duration-picker-status");
    const sync = () => {
      const total = Number(hours.value) * 3600 + Number(minutes.value) * 60 + Number(seconds.value);
      hidden.value = String(total);
      status.textContent = `${Number(hours.value) ? `${Number(hours.value)}h ` : ""}${Number(minutes.value)}m ${Number(seconds.value)}s`;
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
    };
    [hours, minutes, seconds].forEach((select) => select.addEventListener("change", sync));
    picker.querySelector(".duration-zero")?.addEventListener("click", sync);
    picker.querySelector(".duration-clear")?.addEventListener("click", () => {
      hours.value = "0";
      minutes.value = "0";
      seconds.value = "0";
      hidden.value = "";
      status.textContent = "Not entered — choose a duration";
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
}

export function wireBoundedNumberInputs(root) {
  root.querySelectorAll('input[type="number"][min], input[type="number"][max]').forEach((input) => {
    const constrain = (roundStep = false) => {
      if (input.value === "" || !Number.isFinite(input.valueAsNumber)) return;
      let value = input.valueAsNumber;
      if (input.min !== "") value = Math.max(Number(input.min), value);
      if (input.max !== "") value = Math.min(Number(input.max), value);
      if (roundStep && input.step && input.step !== "any") {
        const step = Number(input.step);
        const base = input.min === "" ? 0 : Number(input.min);
        if (Number.isFinite(step) && step > 0) value = base + Math.round((value - base) / step) * step;
      }
      input.value = String(Number(value.toFixed(10)));
    };
    input.addEventListener("input", () => constrain(false));
    input.addEventListener("change", () => constrain(true));
    input.addEventListener("blur", () => constrain(true));
    input.addEventListener("keydown", (event) => {
      if (["e", "E", "+"].includes(event.key)) event.preventDefault();
      if (event.key === "-" && Number(input.min) >= 0) event.preventDefault();
      if ([".", ","].includes(event.key) && input.step === "1") event.preventDefault();
    });
  });
}

export function statusLabel(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
