// API paths aligned with main.py (POST /api/scenario/{name}, GET /api/health) and the SSE event name.
const STREAM_URL = "/api/stream";
const SCENARIO_URL = "/api/scenario";
const HEALTH_URL = "/api/health";
const HEALTH_CHECK_AFTER_MS = 2000;
const TELEMETRY_EVENT = "telemetry";
const STALE_AFTER_MS = 5000;
const CLOCK_INTERVAL_MS = 250;
const HISTORY_TICKS = 60;
const CREW_SIZE = 4;
const MINUTES_PER_HOUR = 60;
const SECONDS_PER_MINUTE = 60;
const HOURS_PER_DAY = 24;
const SVG_NS = "http://www.w3.org/2000/svg";
const TREND_WIDTH = 260;
const TREND_HEIGHT = 64;
const TREND_LEFT = 34;
const TREND_RIGHT = 4;
const TREND_TOP = 8;
const TREND_BOTTOM = 16;
const VALUE_TWEEN_MS = 350;
const THEME_KEY = "theme";
const ALERT_HIGHLIGHT_MS = 1600;
const LABELS = {
  metric: {
    heart_rate: "Heart rate", spo2: "SpO₂", resp_rate: "Resp. rate", core_temp: "Core temp",
    hrv_rmssd: "HRV · RMSSD", systolic_bp: "Systolic BP", diastolic_bp: "Diastolic BP",
    pp_o2: "O₂ partial pressure", pp_co2: "CO₂ partial pressure", cabin_pressure: "Cabin pressure",
    cabin_temp: "Cabin temp", rel_humidity: "Rel. humidity", radiation_dose_rate: "Radiation rate",
    pio2: "Inspired O₂ pressure", event_dose: "Event dose",
  },
  status: { nominal: "● Normal", caution: "◉ Watch", warning: "▲ Warning", critical: "! Action", unknown: "? Unknown" },
  decision: { normal: "✓ Normal", watch: "◉ Watch", action: "! Action", unknown: "? Unknown" },
  activity: { resting: "Resting", exercise: "Exercise", sleep: "Sleep", unknown: "Unknown" },
  quality: { ok: "Valid", stale: "stale", missing: "missing", sensor_fault: "sensor fault" },
  scenario: { nominal: "Nominal cruise", hypoxia: "O₂ failure", hypercapnia: "CO₂ rise", solar_storm: "Solar storm", sensor_dropout: "Sensor dropout" },
};
const PRIMARY_METRICS = ["heart_rate", "spo2", "resp_rate", "core_temp"];
const SECONDARY_METRICS = ["hrv_rmssd", "systolic_bp", "diastolic_bp"];
const CABIN_METRICS = ["pp_o2", "pp_co2", "cabin_pressure", "cabin_temp", "rel_humidity", "radiation_dose_rate"];
const DECISION_TONE = { normal: "nominal", watch: "caution", action: "critical", unknown: "unknown" };
const SEVERITY_ORDER = { critical: 3, warning: 2, caution: 1 };
const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const params = new URLSearchParams(window.location.search);
const mockMode = params.get("mock") === "1";
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const lightQuery = window.matchMedia("(prefers-color-scheme: light)");
const valueTweens = new WeakMap();
const crewViews = new Map();
const alertViews = new Map();
const histories = new Map();
const freshAlertIds = new Map();
const cabinViews = new Map();
let frame = null;
let lastReceived = null;
let transportState = "connecting";
let invalidPacket = false;
let wasStale = true;
let scenarioFinished = false;
let healthCheckedAt = 0;
let stream = null;
let stopMock = null;
let clockTimer = null;
let previousTick = null;

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
function tone(node, value) {
  node.dataset.tone = Object.hasOwn(LABELS.status, value) ? value : "unknown";
}
function numeric(value) { return typeof value === "number" && Number.isFinite(value); }
function number(value) { return numeric(value) ? numberFormat.format(value) : "—"; }
function formatClock(iso) {
  const time = new Date(iso);
  return Number.isNaN(time.getTime()) ? "—" : `${time.toISOString().slice(11, 19)}Z`;
}
// A short number transition when a value really changes; written directly when reduced motion is preferred.
function showValue(node, value) {
  const running = valueTweens.get(node);
  if (running) cancelAnimationFrame(running);
  valueTweens.delete(node);
  const previous = node.dataset.value === undefined ? null : Number(node.dataset.value);
  if (!numeric(value)) { node.textContent = "no data"; delete node.dataset.value; return; }
  node.dataset.value = String(value);
  if (reducedMotion.matches || !numeric(previous) || previous === value) { node.textContent = number(value); return; }
  const start = performance.now();
  const step = now => {
    const progress = Math.min(1, (now - start) / VALUE_TWEEN_MS);
    node.textContent = number(previous + (value - previous) * (1 - (1 - progress) ** 3));
    if (progress < 1) valueTweens.set(node, requestAnimationFrame(step));
    else valueTweens.delete(node);
  };
  valueTweens.set(node, requestAnimationFrame(step));
}
function currentTheme() {
  return document.documentElement.dataset.theme ?? (lightQuery.matches ? "light" : "dark");
}
function configureTheme() {
  const button = document.getElementById("theme-toggle");
  const sync = () => { button.textContent = currentTheme() === "light" ? "◐ Theme: Light" : "◐ Theme: Dark"; };
  button.addEventListener("click", () => {
    const next = currentTheme() === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch { /* storage unavailable: applies to this session only */ }
    sync();
  });
  lightQuery.addEventListener("change", sync);
  sync();
}
function packetStale() {
  if (lastReceived === null || invalidPacket) return true;
  // When the scenario is over the backend stops sending frames; that is the final state, not data loss.
  return !scenarioFinished && performance.now() - lastReceived > STALE_AFTER_MS;
}
async function checkScenarioFinished() {
  healthCheckedAt = performance.now();
  try {
    const response = await fetch(HEALTH_URL, {
      signal: AbortSignal.timeout(STALE_AFTER_MS), redirect: "error", credentials: "same-origin",
    });
    if (!response.ok) return;
    const health = await response.json();
    const finished = health.finished === true && health.scenario === frame?.scenario;
    if (finished !== scenarioFinished) { scenarioFinished = finished; render(); }
  } catch { /* backend unreachable: the usual NO DATA behaviour */ }
}
function validReading(reading) { return reading && numeric(reading.value) && reading.quality === "ok"; }
function missionTime(seconds) {
  if (!numeric(seconds)) return "— d — h — min";
  const totalMinutes = Math.floor(seconds / SECONDS_PER_MINUTE);
  const totalHours = Math.floor(totalMinutes / MINUTES_PER_HOUR);
  return `${Math.floor(totalHours / HOURS_PER_DAY)} d ${totalHours % HOURS_PER_DAY} h ${totalMinutes % MINUTES_PER_HOUR} min`;
}
function createReading(metric, secondary = false, baseline = false) {
  const root = el("div", `reading${secondary ? " secondary" : ""}`);
  root.dataset.metric = metric;
  const label = el("span", "reading-label", LABELS.metric[metric]);
  const output = el("div", "reading-output");
  const value = el("strong", "reading-value", "no data");
  const unit = el("span", "reading-unit");
  output.append(value, unit);
  const delta = el("span", "baseline");
  const status = el("span", "reading-status", LABELS.status.unknown);
  const quality = el("span", "quality");
  quality.hidden = true;
  root.append(label, output);
  if (baseline) root.append(delta);
  root.append(status, quality);
  tone(root, "unknown");
  return { root, value, unit, delta, status, quality };
}
function updateReading(view, reading, stale, unknownDecision = false) {
  const usable = !stale && validReading(reading);
  const state = usable && !unknownDecision ? reading.status : "unknown";
  tone(view.root, state);
  showValue(view.value, usable ? reading.value : null);
  view.root.title = usable ? `${LABELS.metric[view.root.dataset.metric]} · ${formatClock(reading.ts)}` : "";
  view.value.classList.toggle("no-value", !usable);
  view.unit.textContent = usable ? reading.unit : "";
  view.status.textContent = LABELS.status[state] ?? LABELS.status.unknown;
  const delta = usable && numeric(reading.baseline_delta) ? reading.baseline_delta : null;
  view.delta.textContent = delta === null ? "vs baseline: —"
    : `${delta < 0 ? "−" : "+"}${number(Math.abs(delta))} ${reading.unit} vs baseline`;
  const quality = stale ? "stale" : reading?.quality ?? "missing";
  view.quality.hidden = quality === "ok";
  view.quality.textContent = LABELS.quality[quality] ?? "yok";
}
function createCrew(index) {
  const root = el("article", "crew-card");
  const header = el("div", "crew-header");
  const identity = el("div", "crew-identity");
  const avatar = el("span", "crew-number", String(index + 1).padStart(2, "0"));
  const identityText = el("div");
  const name = el("h3", "crew-name", `Crew member ${index + 1}`);
  const role = el("p", "crew-role", "Role pending");
  identityText.append(name, role);
  identity.append(avatar, identityText);
  const decision = el("span", "badge decision", LABELS.decision.unknown);
  const activity = el("span", "activity", "Bilinmiyor");
  header.append(identity, decision, activity);
  const primary = el("div", "primary-readings");
  const secondary = el("div", "secondary-readings");
  const readings = new Map();
  for (const metric of [...PRIMARY_METRICS, ...SECONDARY_METRICS]) {
    const view = createReading(metric, SECONDARY_METRICS.includes(metric), true);
    readings.set(metric, view);
    (SECONDARY_METRICS.includes(metric) ? secondary : primary).append(view.root);
  }
  const trendArea = el("div", "trend-area");
  const trendLabel = el("span", "trend-label", `HEART RATE · LAST ${HISTORY_TICKS} TICKS`);
  const trend = document.createElementNS(SVG_NS, "svg");
  trend.setAttribute("viewBox", `0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`);
  trend.setAttribute("role", "img");
  trend.setAttribute("aria-label", "Heart-rate trend: no data yet");
  trend.classList.add("trend");
  trendArea.append(trendLabel, trend);
  const why = el("button", "why-button", "Why? ↗");
  why.type = "button";
  why.setAttribute("aria-expanded", "false");
  why.setAttribute("aria-controls", `crew-evidence-${index}`);
  const evidence = el("div", "crew-evidence");
  evidence.id = `crew-evidence-${index}`;
  evidence.hidden = true;
  why.addEventListener("click", () => {
    evidence.hidden = !evidence.hidden;
    why.setAttribute("aria-expanded", String(!evidence.hidden));
  });
  const stamp = el("time", "reading-time", "Measured: no data");
  root.append(header, primary, secondary, trendArea, stamp, why, evidence);
  tone(root, "unknown");
  return { root, name, role, decision, activity, readings, trend, stamp, evidence, why, evidenceKey: null };
}
function svgNode(tag, attributes, text = "") {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text) node.textContent = text;
  return node;
}
function drawTrend(svg, entries, unavailable) {
  svg.replaceChildren();
  const values = unavailable ? [] : entries.filter(entry => numeric(entry.value));
  svg.setAttribute("aria-label", values.length
    ? "Heart-rate trend, dashed line is the personal baseline, gaps are missing readings" : "Heart-rate trend: no data");
  if (!values.length) return;
  const baseline = [...entries].reverse().find(entry => numeric(entry.baseline))?.baseline ?? null;
  const all = numeric(baseline) ? [...values.map(entry => entry.value), baseline] : values.map(entry => entry.value);
  const minimum = Math.min(...all);
  const maximum = Math.max(...all);
  const span = maximum - minimum;
  const left = TREND_LEFT;
  const right = TREND_WIDTH - TREND_RIGHT;
  const top = TREND_TOP;
  const bottom = TREND_HEIGHT - TREND_BOTTOM;
  const yOf = value => (span === 0 ? (top + bottom) / 2 : bottom - (value - minimum) / span * (bottom - top));
  const label = value => String(Math.round(value * 10) / 10);
  svg.append(svgNode("text", { x: left - 3, y: top + 3, class: "trend-axis trend-axis-end" }, label(maximum)),
    svgNode("text", { x: left - 3, y: bottom, class: "trend-axis trend-axis-end" }, label(minimum)),
    svgNode("text", { x: left, y: TREND_HEIGHT - 3, class: "trend-axis" }, `−${HISTORY_TICKS}`),
    svgNode("text", { x: right, y: TREND_HEIGHT - 3, class: "trend-axis trend-axis-end" }, "now"));
  if (numeric(baseline)) {
    svg.append(svgNode("line", { x1: left, x2: right, y1: yOf(baseline), y2: yOf(baseline), class: "trend-baseline" }));
  }
  const lastTick = entries.at(-1).tick;
  let segment = [];
  let previous = null;
  function flush() {
    if (!segment.length) return;
    svg.append(svgNode("polyline", { points: segment.join(" ") }));
    if (segment.length === 1) {
      const [x, y] = segment[0].split(",");
      svg.append(svgNode("circle", { cx: x, cy: y, r: "2" }));
    }
    segment = [];
  }
  for (const entry of entries) {
    if (!numeric(entry.value) || (previous !== null && entry.tick !== previous + 1)) flush();
    if (numeric(entry.value)) {
      const x = left + (entry.tick - lastTick + HISTORY_TICKS - 1) / (HISTORY_TICKS - 1) * (right - left);
      segment.push(`${x.toFixed(2)},${yOf(entry.value).toFixed(2)}`);
    }
    previous = entry.tick;
  }
  flush();
}
function evidenceList(why) {
  const list = el("ul", "evidence-list");
  for (const item of why ?? []) {
    const row = el("li");
    const values = `${item.metric} · ${item.value === null ? "no data" : number(item.value)} ${item.comparator} ${item.threshold === null ? "—" : number(item.threshold)}`;
    const time = el("time", "evidence-time", item.ts);
    time.dateTime = item.ts;
    row.append(el("strong", "evidence-values", values), el("p", "evidence-note", item.note), time);
    list.append(row);
  }
  return list;
}
function crewEvidence(view, member, alerts, stale) {
  const key = JSON.stringify([stale, member?.decision, alerts]);
  if (view.evidenceKey === key) return;
  view.evidenceKey = key;
  view.evidence.replaceChildren();
  if (stale) {
    view.evidence.append(el("p", "empty-state", "NO DATA · Waiting for current evidence."));
  } else if (!alerts.length) {
    view.evidence.append(el("p", "empty-state", member.decision === "unknown"
      ? "Insufficient data, no decision possible" : "No active alerts"));
  } else {
    for (const alert of alerts) {
      const block = el("section", "evidence-block");
      const badge = el("span", "badge", LABELS.status[alert.severity]);
      tone(badge, alert.severity);
      block.append(badge, el("p", "alert-message", alert.message),
        el("p", "alert-meta", `${alert.consecutive_count} consecutive readings`), evidenceList(alert.why));
      view.evidence.append(block);
    }
  }
}
function updateCrew(stale) {
  for (let index = 0; index < CREW_SIZE; index += 1) {
    const view = crewViews.get(index);
    const member = frame?.crew[index];
    const decision = stale ? "unknown" : member?.decision ?? "unknown";
    tone(view.root, DECISION_TONE[decision]);
    tone(view.decision, DECISION_TONE[decision]);
    view.name.textContent = member?.name ?? `Crew member ${index + 1}`;
    view.role.textContent = member?.role ?? "Role pending";
    view.decision.textContent = stale ? "? NO DATA" : LABELS.decision[decision] ?? LABELS.decision.unknown;
    view.activity.textContent = `◌ ${LABELS.activity[stale ? "unknown" : member?.activity] ?? LABELS.activity.unknown}`;
    view.why.setAttribute("aria-label", `${member?.name ?? `Crew member ${index + 1}`}: Why?`);
    const stamped = !stale && member?.vitals?.heart_rate?.ts;
    view.stamp.textContent = stamped ? `Measured ${formatClock(stamped)} · SIMULATED` : "Measured: no data";
    if (stamped) view.stamp.dateTime = stamped; else view.stamp.removeAttribute("datetime");
    view.root.classList.toggle("stale-data", stale);
    for (const [metric, reading] of view.readings) updateReading(reading, member?.vitals[metric], stale, decision === "unknown");
    drawTrend(view.trend, histories.get(member?.id) ?? [], stale);
    crewEvidence(view, member, frame?.alerts.filter(alert => alert.crew_id === member?.id) ?? [], stale);
  }
}
function updateCabin(stale) {
  const cabin = frame?.cabin;
  const state = stale ? "unknown" : cabin?.status ?? "unknown";
  const badge = document.getElementById("cabin-status");
  tone(badge, state);
  badge.textContent = stale ? "? NO DATA" : LABELS.status[state];
  for (const [metric, view] of cabinViews) updateReading(view, cabin?.[metric], stale);
  const cabinStamp = document.getElementById("cabin-time");
  const cabinTs = !stale && cabin?.pp_o2?.ts;
  cabinStamp.textContent = cabinTs ? `Measured ${formatClock(cabinTs)} · SIMULATED` : "Measured: no data";
  if (cabinTs) cabinStamp.dateTime = cabinTs; else cabinStamp.removeAttribute("datetime");
  document.getElementById("cabin-readings").classList.toggle("stale-data", stale);
  for (const field of ["hours_until_o2_limit", "hours_until_co2_limit"]) {
    const root = document.getElementById(field);
    const hours = cabin?.[field];
    const output = root.querySelector("strong");
    const note = root.querySelector("small");
    tone(root, stale || !numeric(hours) ? "unknown" : hours === 0 ? "critical" : state);
    root.classList.toggle("neutral-countdown", !stale && !numeric(hours));
    if (stale) { output.textContent = "NO DATA"; note.textContent = "Waiting for current telemetry"; }
    else if (!numeric(hours)) { output.textContent = "—"; note.textContent = "Not approaching limit / cannot be computed"; }
    else if (hours === 0) { output.textContent = "LIMIT EXCEEDED"; note.textContent = "Simulation time"; }
    else {
      const minutes = Math.round(hours * MINUTES_PER_HOUR);
      output.textContent = `${Math.floor(minutes / MINUTES_PER_HOUR)} h ${minutes % MINUTES_PER_HOUR} min`;
      note.textContent = "Simulation time";
    }
  }
}
function updateAlerts(stale) {
  const container = document.getElementById("alerts");
  const notice = document.getElementById("alerts-notice");
  const alerts = stale ? [] : [...frame.alerts].sort((a, b) =>
    (SEVERITY_ORDER[b.severity] ?? 0) - (SEVERITY_ORDER[a.severity] ?? 0)
    || Date.parse(b.last_seen) - Date.parse(a.last_seen));
  document.getElementById("alert-count").textContent = stale ? "—" : `${alerts.length} ACTIVE`;
  notice.hidden = alerts.length > 0;
  notice.textContent = stale ? "NO DATA · Current alert status unknown." : "✓ No active alerts";
  container.hidden = stale;
  if (stale) return;
  const activeIds = new Set(alerts.map(alert => alert.id));
  for (const [id, view] of alertViews) {
    if (!activeIds.has(id)) { view.root.remove(); alertViews.delete(id); }
  }
  alerts.forEach((alert, index) => {
    let view = alertViews.get(alert.id);
    if (!view) {
      const root = el("details", "alert-row");
      const summary = el("summary");
      const heading = el("span", "alert-heading");
      const owner = el("strong", "alert-owner");
      const severity = el("span", "alert-severity");
      const message = el("span", "alert-message");
      const meta = el("span", "alert-meta");
      const body = el("div", "alert-evidence");
      heading.append(owner, severity);
      summary.append(heading, message, meta);
      root.append(summary, body);
      view = { root, owner, severity, message, meta, body, key: null };
      alertViews.set(alert.id, view);
    }
    tone(view.root, alert.severity);
    view.root.classList.toggle("new-alert", performance.now() - (freshAlertIds.get(alert.id) ?? -Infinity) < ALERT_HIGHLIGHT_MS);
    view.owner.textContent = alert.crew_id === null ? "Cabin" : frame.crew.find(member => member.id === alert.crew_id)?.name ?? alert.crew_id;
    view.severity.textContent = LABELS.status[alert.severity];
    view.message.textContent = alert.message;
    view.meta.textContent = `${alert.consecutive_count} consecutive readings · Last: ${formatClock(alert.last_seen)}`;
    const key = JSON.stringify(alert.why);
    if (key !== view.key) { view.body.replaceChildren(evidenceList(alert.why)); view.key = key; }
    if (container.children[index] !== view.root) container.insertBefore(view.root, container.children[index] ?? null);
  });
}
function render() {
  const stale = packetStale();
  document.getElementById("simulated").hidden = frame?.simulated === false;
  document.getElementById("scenario").textContent = LABELS.scenario[frame?.scenario] ?? "Waiting for packet";
  document.getElementById("mission_elapsed_s").textContent = missionTime(frame?.mission_elapsed_s);
  document.getElementById("mission_elapsed_s").classList.toggle("muted", stale);
  updateCrew(stale); updateCabin(stale); updateAlerts(stale);
  wasStale = stale;
  updateClock();
}
function updateClock() {
  const stale = packetStale();
  if (stale !== wasStale) { render(); return; }
  const seconds = lastReceived === null ? null : (performance.now() - lastReceived) / 1000;
  if (!mockMode && !scenarioFinished && seconds !== null && seconds * 1000 > HEALTH_CHECK_AFTER_MS
    && performance.now() - healthCheckedAt > HEALTH_CHECK_AFTER_MS) checkScenarioFinished();
  document.getElementById("packet-age").textContent = seconds === null ? "No packet yet" : `Last packet: ${number(Math.floor(seconds))} s ago`;
  const connection = document.getElementById("connection");
  const connected = transportState === "open" && !stale;
  connection.textContent = scenarioFinished ? "■ Scenario complete"
    : connected ? "● Connected" : transportState === "closed" ? "× Disconnected" : "◌ Reconnecting";
  tone(connection, connected || scenarioFinished ? "nominal" : "unknown");
  const notice = document.getElementById("data-notice");
  notice.hidden = !stale && !scenarioFinished;
  notice.textContent = scenarioFinished ? "Scenario complete · showing the last packet. Choose a new scenario."
    : invalidPacket ? "NO DATA · Telemetry packet unreadable; waiting for a valid packet."
    : "NO DATA · Waiting for current telemetry. Old readings are never treated as normal.";
}
function acceptFrame(incoming) {
  // The full schema belongs to the backend; a malformed or other-version packet must never look normal.
  if (!incoming || incoming.contract_version !== "0.2.0" || !Number.isInteger(incoming.tick)
    || !Array.isArray(incoming.crew) || incoming.crew.length !== CREW_SIZE
    || !incoming.cabin || !Array.isArray(incoming.alerts)
    || incoming.crew.some(member => !member?.vitals || !member.id)) {
    invalidPacket = true; render(); return;
  }
  const interrupted = packetStale();
  if (previousTick !== null && incoming.tick <= previousTick) histories.clear();
  const previousIds = new Set(frame?.alerts.map(alert => alert.id) ?? []);
  for (const [id, firstSeen] of freshAlertIds) {
    if (performance.now() - firstSeen >= ALERT_HIGHLIGHT_MS) freshAlertIds.delete(id);
  }
  for (const alert of incoming.alerts) {
    if (!previousIds.has(alert.id)) freshAlertIds.set(alert.id, performance.now());
  }
  for (const member of incoming.crew) {
    const history = histories.get(member.id) ?? [];
    if (interrupted && history.length) history.push({ tick: incoming.tick - 0.5, value: null, baseline: null });
    const reading = member.vitals.heart_rate;
    const valid = validReading(reading);
    history.push({
      tick: incoming.tick,
      value: valid ? reading.value : null,
      baseline: valid && numeric(reading.baseline_delta) ? reading.value - reading.baseline_delta : null,
    });
    histories.set(member.id, history.filter(entry => entry.tick > incoming.tick - HISTORY_TICKS));
  }
  frame = incoming;
  previousTick = incoming.tick;
  lastReceived = performance.now();
  scenarioFinished = false;
  invalidPacket = false;
  transportState = "open";
  render();
}
function configureDemo() {
  if (params.get("demo") !== "1") return;
  document.getElementById("demo-panel").hidden = false;
  const buttons = document.getElementById("scenario-buttons");
  for (const [scenario, label] of Object.entries(LABELS.scenario)) {
    const button = el("button", "scenario-button", label);
    button.type = "button";
    button.addEventListener("click", async () => {
      const result = document.getElementById("demo-result");
      button.disabled = true;
      result.textContent = "Requesting scenario…";
      try {
        const headers = {};
        const demoKey = document.getElementById("demo-key").value;
        if (demoKey) headers["X-Demo-Key"] = demoKey;
        const response = await fetch(`${SCENARIO_URL}/${encodeURIComponent(scenario)}`, {
          method: "POST", headers,
          signal: AbortSignal.timeout(STALE_AFTER_MS), redirect: "error", credentials: "same-origin",
        });
        if (response.status === 401) { result.textContent = "Demo key required or incorrect."; return; }
        if (!response.ok) throw new Error("scenario unavailable");
        result.textContent = mockMode ? "Request sent. The local mock stream runs on its own schedule." : "Scenario requested; waiting for telemetry.";
      } catch { result.textContent = "Backend not ready yet"; }
      finally { button.disabled = false; }
    });
    buttons.append(button);
  }
}
async function start() {
  for (let index = 0; index < CREW_SIZE; index += 1) {
    const view = createCrew(index);
    crewViews.set(index, view);
    document.getElementById("crew-grid").append(view.root);
  }
  for (const metric of CABIN_METRICS) {
    const view = createReading(metric, true);
    cabinViews.set(metric, view);
    document.getElementById("cabin-readings").append(view.root);
  }
  configureTheme(); configureDemo(); render();
  clockTimer = window.setInterval(updateClock, CLOCK_INTERVAL_MS);
  if (mockMode) {
    document.getElementById("source-label").textContent = "LOCAL MOCK · Decisions are fixed demo examples";
    try {
      const { startMock } = await import("./mock.js");
      stopMock = startMock(acceptFrame);
    } catch { transportState = "closed"; invalidPacket = true; render(); }
  } else {
    try {
      stream = new EventSource(STREAM_URL);
      stream.addEventListener("open", () => { transportState = "open"; updateClock(); });
      stream.addEventListener(TELEMETRY_EVENT, event => {
        try { acceptFrame(JSON.parse(event.data)); }
        catch { invalidPacket = true; render(); }
      });
      stream.addEventListener("error", () => {
        transportState = stream.readyState === EventSource.CLOSED ? "closed" : "connecting";
        updateClock();
      });
    } catch { transportState = "closed"; updateClock(); }
  }
}
window.addEventListener("pagehide", () => { stream?.close(); stopMock?.(); window.clearInterval(clockTimer); });
window.addEventListener("pageshow", event => { if (event.persisted) window.location.reload(); });
start();
