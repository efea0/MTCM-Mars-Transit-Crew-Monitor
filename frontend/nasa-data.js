const GROUPS = { cabin: "Cabin atmosphere", vitals: "Personal vital deviations", radiation: "Radiation" };
const VERIFICATION = {
  verified: ["✓ Verified", "verified"], partial: ["◐ Partially verified", "partial"],
  unverified: ["! Unverified", "unverified"], unknown: ["? Verification unknown", "unknown"],
};
const TYPES = { FLR: "Flare", CME: "Coronal mass ejection", SEP: "Solar energetic particles" };
const REQUEST_TIMEOUT_MS = 8000;
function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  element.className = className; element.textContent = text;
  return element;
}
function button(text, className = "nasa-button") {
  const element = node("button", className, text); element.type = "button"; return element;
}
function finite(value) { return typeof value === "number" && Number.isFinite(value); }
export function safeNasaUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      && (url.hostname === "nasa.gov" || url.hostname.endsWith(".nasa.gov")) ? url.href : null;
  } catch { return null; }
}
export function validateLimits(payload) {
  if (!payload || !Array.isArray(payload.items)) throw new Error("Invalid limits response");
  const ids = new Set();
  for (const item of payload.items) {
    if (!item || typeof item.id !== "string" || !item.id || ids.has(item.id)
      || !Object.hasOwn(GROUPS, item.group) || typeof item.label !== "string"
      || (item.threshold !== null && !finite(item.threshold))) throw new Error("Invalid limit row");
    ids.add(item.id);
  }
  return payload;
}
export function validateSep(payload) {
  if (!payload || typeof payload.sep_id !== "string" || !Number.isFinite(Date.parse(payload.event_time))
    || !Array.isArray(payload.instruments) || payload.instruments.some(value => typeof value !== "string")
    || !Array.isArray(payload.linked_events)) throw new Error("Invalid DONKI response");
  for (const event of payload.linked_events) {
    if (!event || typeof event.activity_id !== "string" || typeof event.type !== "string"
      || !Number.isFinite(Date.parse(event.time))) throw new Error("Invalid linked event");
  }
  return payload;
}
export function linkedTimeline(payload) {
  return [...payload.linked_events, { activity_id: payload.sep_id, type: "SEP", time: payload.event_time }]
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time) || a.activity_id.localeCompare(b.activity_id));
}
export function findLimit(payload, ruleId) { return payload?.items.find(item => item.id === ruleId) ?? null; }
export async function fetchNasaResponse(path, mock = false) {
  if (mock) return (await import("./mock.js")).mockNasaResponse(path);
  const response = await fetch(path, { credentials: "same-origin", redirect: "error", signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
  if (!response.ok) throw new Error(`Reference request failed (${response.status})`);
  return response.json();
}
function timestamp(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime()) ? date.toISOString().replace("T", " ").replace(/\.000Z$/, " UTC") : "Unavailable";
}
function verification(item, mock) {
  const [label, kind] = VERIFICATION[item.status] ?? VERIFICATION.unknown;
  return node("span", `nasa-verification ${kind}`, `${label}${mock ? " · mock" : ""}`);
}
function link(label, value) {
  const url = safeNasaUrl(value);
  if (!url) return node("span", "nasa-muted", `${label}: unavailable`);
  const element = node("a", "nasa-link", `${label} ↗`);
  element.href = url; element.target = "_blank"; element.rel = "noopener noreferrer";
  return element;
}
export function createNasaData({ trigger, banner, mock = false, load = path => fetchNasaResponse(path, mock) }) {
  let limits = null;
  let sep = null;
  let limitsState = "idle";
  let sepState = "idle";
  let limitsTask = null;
  let sepTask = null;
  let lastFocus = null;
  let selectedRule = null;
  const dialog = node("dialog", "nasa-dialog");
  dialog.id = "nasa-data"; dialog.setAttribute("aria-labelledby", "nasa-title");
  dialog.setAttribute("aria-describedby", "nasa-disclaimer");
  const head = node("header", "nasa-header");
  const intro = node("div"); intro.append(node("p", "nasa-kicker", "NASA / EVIDENCE & PROVENANCE"));
  const title = node("h2", "", "NASA data & limits"); title.id = "nasa-title";
  intro.append(title);
  const close = button("Close ✕"); close.setAttribute("aria-label", "Close NASA data and limits");
  head.append(intro, close);
  const disclaimer = node("p", "nasa-disclaimer", mock
    ? "LOCAL MOCK · All reference records below are test fixtures, including verification badges. They are not verified NASA data."
    : "NASA reference limits and a recorded DONKI event. Crew health, exposure and predicted dose are simulated. This is decision support, not a medical diagnosis.");
  disclaimer.id = "nasa-disclaimer";
  const nav = node("nav", "nasa-nav"); nav.setAttribute("aria-label", "NASA data sections");
  const tableButton = button("Evidence & limits");
  const eventButton = button("DONKI event chain");
  nav.append(tableButton, eventButton);
  const content = node("div", "nasa-content");
  const limitsSection = node("section", "nasa-section"); limitsSection.id = "nasa-limits";
  const limitsTitle = node("h3", "", "Evidence & limits"); limitsTitle.tabIndex = -1;
  const version = node("p", "nasa-muted");
  const limitNotice = node("p", "nasa-load-state", "Loading reference limits…"); limitNotice.setAttribute("role", "status");
  const limitRetry = button("Retry limits"); limitRetry.hidden = true;
  const tables = node("div", "nasa-tables");
  limitsSection.append(limitsTitle, version, node("p", "nasa-muted", "Verified = source value checked. Partial = interpretation or conversion is ours. Unverified = demo parameter or team assumption; not an official medical threshold."), limitNotice, limitRetry, tables);
  const sepSection = node("section", "nasa-section"); sepSection.id = "nasa-sep";
  const sepTitle = node("h3", "", "DONKI event chain"); sepTitle.tabIndex = -1;
  const sepNotice = node("p", "nasa-load-state", "Loading recorded solar event…"); sepNotice.setAttribute("role", "status");
  const sepRetry = button("Retry DONKI event"); sepRetry.hidden = true;
  const eventCard = node("div", "nasa-event");
  sepSection.append(sepTitle, sepNotice, sepRetry, eventCard);
  content.append(limitsSection, sepSection);
  dialog.append(head, disclaimer, nav, content); document.body.append(dialog);
  trigger.setAttribute("aria-haspopup", "dialog"); trigger.setAttribute("aria-controls", dialog.id);
  banner.type = "button";
  banner.setAttribute("aria-controls", dialog.id); banner.setAttribute("aria-haspopup", "dialog");
  function scrollTo(element) {
    element.scrollIntoView({ block: "start", behavior: "instant" });
    element.focus({ preventScroll: true });
  }
  function highlightRule() {
    for (const row of tables.querySelectorAll("tr[data-rule-id]")) {
      row.classList.toggle("nasa-selected", row.dataset.ruleId === selectedRule);
      if (row.dataset.ruleId === selectedRule) scrollTo(row);
    }
  }
  function open(section = "limits", ruleId = null) {
    lastFocus = document.activeElement;
    selectedRule = ruleId;
    if (!dialog.open) dialog.showModal();
    void ensureLimits(); void ensureSep();
    if (section === "sep") scrollTo(sepTitle);
    else if (!selectedRule) dialog.scrollTop = 0;
    if (selectedRule) highlightRule();
  }
  trigger.addEventListener("click", () => open());
  banner.addEventListener("click", () => open("sep"));
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => lastFocus?.isConnected && lastFocus.focus({ preventScroll: true }));
  tableButton.addEventListener("click", () => scrollTo(limitsTitle));
  eventButton.addEventListener("click", () => scrollTo(sepTitle));
  function renderLimits() {
    tables.replaceChildren();
    version.textContent = `Reference version ${limits.version ?? "unavailable"} · ${limits.items.length} rules · ${mock ? "LOCAL MOCK" : "GET /api/limits"}`;
    limitNotice.textContent = limits.items.length ? "" : "No reference limits were returned. No thresholds are assumed.";
    for (const [group, label] of Object.entries(GROUPS)) {
      const section = node("section", "nasa-limit-group");
      const groupTitle = node("h4", "", label); section.append(groupTitle);
      if (group === "vitals") section.append(node("p", "nasa-demo-warning", "DEMO PARAMETERS · Personal vital deviation thresholds are team parameters, not verified NASA medical limits."));
      const items = limits.items.filter(item => item.group === group);
      if (!items.length) { section.append(node("p", "nasa-muted", "No limits available for this group.")); tables.append(section); continue; }
      const wrap = node("div", "nasa-table-wrap"); wrap.tabIndex = 0; wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", `${label} limits table`);
      const table = node("table", "nasa-table");
      const caption = node("caption", "", `${label} · values and sources from ${mock ? "local mock fixtures" : "/api/limits"}`);
      const thead = node("thead"); const headings = node("tr");
      for (const label of ["Rule / metric", "Limit", "Trigger", "Verification", "Source"]) { const th = node("th", "", label); th.scope = "col"; headings.append(th); }
      thead.append(headings); const tbody = node("tbody");
      for (const item of items) {
        const row = node("tr"); row.dataset.ruleId = item.id; row.tabIndex = -1;
        const name = node("th"); name.scope = "row";
        name.append(node("strong", "", item.label), node("code", "nasa-rule-id", item.id), node("span", "nasa-muted", item.metric ?? "Metric not specified"));
        const value = node("td", "nasa-limit-value", finite(item.threshold) ? `${item.comparator ?? ""} ${item.threshold} ${item.unit ?? ""}` : "Limit unavailable");
        const count = Number.isInteger(item.consecutive_required) ? `${item.consecutive_required} consecutive readings` : "Count not specified";
        const condition = node("td", "", `${item.severity ?? "Unspecified severity"} · ${count}`);
        const status = node("td"); status.append(verification(item, mock));
        if (item.status === "unverified") status.append(node("small", "nasa-muted", "Demo parameter / assumption"));
        const source = node("td", "nasa-source-text", item.source || "Source unavailable");
        row.append(name, value, condition, status, source); tbody.append(row);
      }
      table.append(caption, thead, tbody); wrap.append(table); section.append(wrap); tables.append(section);
    }
    if (selectedRule) highlightRule();
  }
  function renderSep() {
    eventCard.replaceChildren();
    const title = node("h4", "", sep.sep_id);
    const meta = node("p", "nasa-muted", `Event ${timestamp(sep.event_time)} · Retrieved ${timestamp(sep.fetched_at)}`);
    const provenance = node("p", "nasa-event-note", mock ? "LOCAL MOCK · Test event and linked records. Dose is simulated." : sep.note || "DONKI records the event time and type. All dose data in this app is simulated.");
    const instruments = node("p", "", `Instruments: ${sep.instruments.join(" · ") || "Unavailable"}`);
    const links = node("p", "nasa-event-links"); links.append(link("Open DONKI event", sep.link), link("NASA API source", sep.source));
    const timeline = node("ol", "nasa-timeline"); timeline.setAttribute("aria-label", "Flare, CME and SEP record timeline");
    const events = linkedTimeline(sep);
    for (const type of ["FLR", "CME", "SEP"]) {
      const entries = events.filter(event => event.type === type);
      const step = node("li", `nasa-stage nasa-stage-${type.toLowerCase()}`);
      step.append(node("span", "nasa-kicker", type), node("strong", "", TYPES[type]));
      step.append(node("time", "", entries.length ? timestamp(entries[0].time) : "No linked record"));
      step.append(node("small", "nasa-muted", entries.length ? `${entries.length} ${type === "SEP" ? "event" : "linked records · earliest shown"}` : "No event inferred"));
      timeline.append(step);
    }
    const details = node("details", "nasa-linked-events");
    details.append(node("summary", "", `Inspect ${sep.linked_events.length} linked activities · chronological order`));
    const eventList = node("ol");
    for (const event of events.filter(event => event.type !== "SEP")) {
      const item = node("li"); item.append(node("strong", "", TYPES[event.type] ?? event.type), node("time", "", timestamp(event.time)), node("code", "", event.activity_id)); eventList.append(item);
    }
    if (!eventList.children.length) details.append(node("p", "nasa-muted", "No linked activities were returned. A flare or CME is not inferred."));
    details.append(eventList);
    eventCard.append(title, meta, provenance, instruments, links, timeline, node("p", "nasa-muted", "Linked records grouped as flare → CME → SEP. This is event provenance, not proof of a causal chain or a source of dose measurements."), details);
  }
  function fillSource(root) {
    const ruleId = root.dataset.nasaRule;
    const item = findLimit(limits, ruleId);
    const state = `${limitsState}:${ruleId}:${item?.id ?? ""}`;
    if (root.dataset.sourceState === state) return;
    root.dataset.sourceState = state; root.replaceChildren();
    if (item) {
      root.append(verification(item, mock), node("span", "", item.source || "Source unavailable"));
      if (item.status === "unverified") root.append(node("span", "nasa-muted", "Demo parameter, not a verified NASA threshold."));
      const inspect = button(`Inspect rule: ${ruleId}`, "nasa-rule-link"); inspect.addEventListener("click", () => open("limits", ruleId)); root.append(inspect);
    } else {
      root.append(node("span", "nasa-muted", limitsState === "ready" ? `Reference not matched · ${ruleId}` : limitsState === "error" ? "Reference unavailable · no verification claimed" : "Loading reference source…"));
    }
  }
  function refreshSources() { for (const root of document.querySelectorAll("[data-nasa-rule]")) fillSource(root); }
  async function ensureLimits() {
    if (limitsState === "ready") return limits;
    if (limitsTask) return limitsTask;
    limitsState = "loading"; limitRetry.hidden = true; limitNotice.textContent = "Loading reference limits…";
    limitsTask = (async () => {
      try { limits = validateLimits(await load("/api/limits")); limitsState = "ready"; renderLimits(); }
      catch { limitsState = "error"; limitNotice.textContent = "Reference limits unavailable. No values or verification status are assumed."; limitRetry.hidden = false; }
      finally { limitsTask = null; refreshSources(); }
      return limits;
    })(); return limitsTask;
  }
  async function ensureSep() {
    if (sepState === "ready") return sep;
    if (sepTask) return sepTask;
    sepState = "loading"; sepRetry.hidden = true; sepNotice.textContent = "Loading recorded solar event…";
    sepTask = (async () => {
      try { sep = validateSep(await load("/api/sep-event")); sepState = "ready"; sepNotice.textContent = ""; renderSep(); updateBanner(); }
      catch { sepState = "error"; sepNotice.textContent = "DONKI record unavailable. No event or activity is inferred."; sepRetry.hidden = false; updateBanner(); }
      finally { sepTask = null; }
      return sep;
    })(); return sepTask;
  }
  function updateBanner() {
    const text = sepState === "ready" ? `${mock ? "MOCK" : "NASA DONKI"} · ${new Date(sep.event_time).toISOString().slice(0, 10)} SEP · Dose simulated ↗`
      : sepState === "error" ? "DONKI record unavailable · inspect ↗" : "NASA DONKI · Loading record · Dose simulated";
    if (banner.textContent !== text) banner.textContent = text;
  }
  limitRetry.addEventListener("click", () => { void ensureLimits(); });
  sepRetry.addEventListener("click", () => { void ensureSep(); });
  // Load small reference catalogue once; solar provenance is fetched only on demand.
  void ensureLimits();
  return {
    open,
    update(frame) {
      const active = frame?.scenario === "solar_storm";
      banner.classList.toggle("nasa-storm-active", active);
      banner.setAttribute("aria-hidden", String(!active)); banner.tabIndex = active ? 0 : -1;
      if (active) { void ensureSep(); updateBanner(); }
    },
    sourceNode(ruleId) {
      const root = node("div", "nasa-rule-source"); root.dataset.nasaRule = ruleId ?? "unspecified"; fillSource(root); return root;
    },
    ready: () => ensureLimits(),
  };
}
