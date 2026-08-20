let cachedConfiguration = null;

export async function loadSystemConfiguration() {
  if (!cachedConfiguration) {
    const response = await fetch("/api/configurator/public", { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load assessment-system configuration.");
    cachedConfiguration = await response.json();
  }
  return cachedConfiguration;
}

function escaped(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function darken(hex, amount = 34) {
  const clean = String(hex || "#4f46e5").replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(clean)) return "#3730a3";
  const parts = [0, 2, 4].map(index => Math.max(0, parseInt(clean.slice(index, index + 2), 16) - amount));
  return `#${parts.map(value => value.toString(16).padStart(2, "0")).join("")}`;
}

function replacementPairs(configuration) {
  const terms = configuration.terminology;
  const factors = configuration.generalFactors || [];
  const pairs = [
    ["Journees", terms.sessionPlural], ["Journee", terms.session],
    ["Recruits", terms.participantPlural], ["Recruit", terms.participant],
    ["Evaluators", terms.assessorPlural], ["Evaluator", terms.assessor],
    ["Rooms", terms.groupPlural], ["Room", terms.group],
    ["Activities", terms.stagePlural], ["Activity", terms.stage],
    ["Punctuality", factors.find(item => item.storageKey === "punctuality")?.name],
    ["Respect to us", factors.find(item => item.storageKey === "respect")?.name],
    ["Seriousness", factors.find(item => item.storageKey === "seriousness")?.name],
  ];
  for (const category of configuration.assessorCategories || []) {
    pairs.push([category.key, category.name]);
  }
  for (const band of configuration.performanceBands || []) pairs.push([band.key, band.name]);
  return pairs.filter(([, replacement]) => replacement);
}

function replaceText(value, configuration) {
  let result = value;
  for (const [source, replacement] of replacementPairs(configuration)) {
    result = result.replace(new RegExp(`\\b${escaped(source)}\\b`, "gi"), match => {
      if (match === match.toUpperCase()) return String(replacement).toUpperCase();
      if (match[0] === match[0].toUpperCase()) return String(replacement);
      return String(replacement).toLowerCase();
    });
  }
  result = result.replace(/\/20\b/g, `/${configuration.scoreMaximum}`);
  return result;
}

function updateNode(node, configuration) {
  if (node.nodeType === Node.TEXT_NODE) {
    const updated = replaceText(node.nodeValue, configuration);
    if (updated !== node.nodeValue) node.nodeValue = updated;
    return;
  }
  if (!(node instanceof Element)) return;
  for (const element of [node, ...node.querySelectorAll("input[placeholder], textarea[placeholder]")]) {
    if (element.placeholder) element.placeholder = replaceText(element.placeholder, configuration);
  }
  for (const child of node.childNodes) updateNode(child, configuration);
}

function applyFeatureVisibility(configuration) {
  const features = configuration.features || {};
  const participant = configuration.participants || {};
  for (const [key, enabled] of Object.entries({
    phone: participant.phoneEnabled,
    dob: participant.dateOfBirthEnabled,
    photo: participant.photoEnabled,
    arrival: participant.arrivalTimeEnabled,
    attendanceComment: participant.attendanceCommentEnabled,
  })) document.body.classList.toggle(`config-no-${key}`, enabled === false);
  const selectors = {
    liveDashboard: '[data-section="dashboard"]',
    participantAttendance: '[data-section="attendance"], [data-tab="attendance"]',
    groupsAndRooms: '[data-section="assignments"]',
    resultsAndRankings: '[data-section="results"], [data-tab="results"]',
    participantProfiles: '[data-section="profiles"], [data-tab="profiles"]',
    managementPortal: 'a[href="/view"].home-destination',
  };
  for (const [key, selector] of Object.entries(selectors)) {
    document.querySelectorAll(selector).forEach(element => element.classList.toggle("hidden", features[key] === false));
  }
  document.querySelectorAll(".attendance-phone").forEach(element => element.closest("label")?.classList.toggle("hidden", participant.phoneEnabled === false));
  document.querySelectorAll(".attendance-dob").forEach(element => element.closest("label")?.classList.toggle("hidden", participant.dateOfBirthEnabled === false));
  document.querySelectorAll(".attendance-arrival").forEach(element => element.closest("label")?.classList.toggle("hidden", participant.arrivalTimeEnabled === false));
  document.querySelectorAll(".attendance-comment").forEach(element => element.closest("label")?.classList.toggle("hidden", participant.attendanceCommentEnabled === false));
  document.querySelectorAll(".attendance-photo-change, .attendance-photo-remove").forEach(element => element.classList.toggle("hidden", participant.photoEnabled === false));
}

function applyDashboardVisibility(configuration) {
  const widgets = configuration.dashboard || {};
  const metricCards = [...document.querySelectorAll(".metric-grid .metric-card")];
  metricCards[0]?.classList.toggle("hidden", widgets.attendanceCounts === false);
  metricCards[1]?.classList.toggle("hidden", widgets.attendanceCounts === false);
  metricCards[2]?.classList.toggle("hidden", widgets.assessorCategories === false);
  metricCards[3]?.classList.toggle("hidden", widgets.activeStage === false);
  const panelRules = [
    ["live submission progress", "submissionProgress"],
    ["unresolved warnings", "warnings"],
    ["lifecycle", "stageLifecycle"],
    ["provisional overall ranking", "provisionalRanking"],
    ["dimension averages", "averages"],
  ];
  document.querySelectorAll(".panel h2").forEach(heading => {
    const text = heading.textContent.trim().toLowerCase();
    const rule = panelRules.find(([phrase]) => text.includes(phrase));
    if (rule) heading.closest(".panel")?.classList.toggle("hidden", widgets[rule[1]] === false);
  });
}

export function applySystemConfiguration(configuration) {
  const root = document.documentElement;
  root.style.setProperty("--red", configuration.branding.primaryColor);
  root.style.setProperty("--red-dark", darken(configuration.branding.primaryColor));
  root.style.setProperty("--red-soft", `${configuration.branding.primaryColor}12`);
  root.style.setProperty("--ink", configuration.branding.darkColor);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", configuration.branding.primaryColor);
  document.querySelectorAll(".brand-mark").forEach(mark => {
    mark.textContent = configuration.branding.shortMark || "AS";
    mark.style.background = configuration.branding.primaryColor;
  });
  let bandStyles = document.querySelector("#configuredBandStyles");
  if (!bandStyles) {
    bandStyles = document.createElement("style");
    bandStyles.id = "configuredBandStyles";
    document.head.appendChild(bandStyles);
  }
  bandStyles.textContent = (configuration.performanceBands || []).map(band =>
    `.color-chip.${band.key}{color:${band.color}} .grade-orb.${band.key}{background:${band.color};color:#fff}`
  ).join("\n");
  document.querySelectorAll(".login-card > .eyebrow, .home-hero > .eyebrow").forEach(element => {
    element.textContent = configuration.branding.organizationName || configuration.name;
  });
  const loginTitle = document.querySelector(".login-card > h1");
  if (loginTitle) loginTitle.textContent = configuration.name;
  const workspaceBrandName = document.querySelector("#workspaceBrandName");
  if (workspaceBrandName) workspaceBrandName.textContent = configuration.name;
  document.querySelectorAll("[data-system-name]").forEach(element => { element.textContent = configuration.name; });
  const homeTitle = document.querySelector(".home-hero > h1");
  if (homeTitle) homeTitle.textContent = configuration.name;
  document.title = `${configuration.name}${document.title.includes("·") ? ` · ${document.title.split("·").at(-1).trim()}` : ""}`;
  updateNode(document.body, configuration);
  applyFeatureVisibility(configuration);
  applyDashboardVisibility(configuration);
  const observer = new MutationObserver(records => {
    observer.disconnect();
    for (const record of records) for (const node of record.addedNodes) updateNode(node, configuration);
    applyFeatureVisibility(configuration);
    applyDashboardVisibility(configuration);
    observer.observe(document.body, { childList: true, subtree: true });
  });
  observer.observe(document.body, { childList: true, subtree: true });
  return configuration;
}

export async function initializeSystemUI() {
  const configuration = await loadSystemConfiguration();
  return applySystemConfiguration(configuration);
}
