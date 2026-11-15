import { createNasaData } from "./nasa-data.js";
import { setText, svgRenderer, chartRange, resetChartRange } from "./ui.js";
import { createFinishCard } from "./scenario-queue.js";
import { createDecisionBand } from "./decisions.js";
import { createSession, validFrame } from "./session.js";
import { createCrewDetail } from "./crew-detail.js";
import { createBeyondSpace } from "./beyond-space.js";

const STREAM_URL = "/api/stream";
const SCENARIO_URL = "/api/scenario";
const CONTROL_URL = "/api/control";
const SPEED_URL = "/api/speed";
// Demo-only presets (simulation seconds per real second). The team default is 600; the server owns the real value.
const SPEED_PRESETS = [150, 300, 600, 1200];
const HEALTH_URL = "/api/health";
const HEALTH_CHECK_AFTER_MS = 2000;
const WAKE_AFTER_MS = 3000;
const RECONNECT_AFTER_MS = 3000;
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
const ALERT_HIGHLIGHT_MS = 1600;
const LABELS = {
  metric: {
    heart_rate: "Heart rate", spo2: "SpO₂", resp_rate: "Respiration", core_temp: "Core temperature",
    hrv_rmssd: "HRV · RMSSD", systolic_bp: "Systolic BP", diastolic_bp: "Diastolic BP",
    pp_o2: "O₂ partial pressure", pp_co2: "CO₂ partial pressure", cabin_pressure: "Cabin pressure",
    cabin_temp: "Cabin temperature", rel_humidity: "Relative humidity", radiation_dose_rate: "Radiation rate",
    pio2: "Inspired O₂ pressure", event_dose: "Event dose",
  },
  status: { nominal: "● Normal", caution: "◉ Watch", warning: "▲ Warning", critical: "! Action", unknown: "? Unknown" },
  decision: { normal: "✓ Normal", watch: "◉ Watch", action: "! Action", unknown: "? Unknown" },
  activity: { resting: "Resting", exercise: "Exercise", sleep: "Sleeping", unknown: "Unknown" },
  quality: { ok: "Valid", stale: "stale", missing: "missing", sensor_fault: "sensor fault" },
  scenario: { nominal: "Nominal cruise", hypoxia: "O₂ failure", hypercapnia: "CO₂ rise", solar_storm: "Solar storm", sensor_dropout: "Sensor dropout" },
  sender: { crew: "Crew", mission_control: "Mission Control", system: "System" },
};
const PRIMARY_METRICS = ["heart_rate", "spo2", "resp_rate", "core_temp"];
const SECONDARY_METRICS = ["hrv_rmssd", "systolic_bp", "diastolic_bp"];
const CABIN_METRICS = ["pp_o2", "pp_co2", "cabin_pressure", "cabin_temp", "rel_humidity", "radiation_dose_rate"];
const CABIN_TREND_METRICS = ["pp_co2", "pp_o2"];
const DECISION_TONE = { normal: "nominal", watch: "caution", action: "critical", unknown: "unknown" };
const SEVERITY_ORDER = { critical: 3, warning: 2, caution: 1 };
const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const readingFormat = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const params = new URLSearchParams(window.location.search);
const mockMode = params.get("mock") === "1";
createBeyondSpace({ trigger: document.getElementById("beyond-trigger") });
const nasaData = createNasaData({ trigger: document.getElementById("nasa-trigger"), banner: document.getElementById("nasa-storm-banner"), mock: mockMode });
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const crewViews = new Map();
const alertViews = new Map();
const histories = new Map();
const session = createSession();
const freshAlertIds = new Map();
const cabinViews = new Map();
const earthViews = new Map();
const cabinTrendViews = new Map();
const thresholdMarks = new Map();
let frame = null;
let lastReceived = null;
let transportState = "connecting";
let connectingSince = performance.now();
let retryTimer = null;
let pageHidden = false;
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
function markNew(node) {
  node.classList.add("is-new");
  node.addEventListener("animationend", () => node.classList.remove("is-new"), { once: true });
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
function showValue(node, value) {
  setText(node, numeric(value) ? readingFormat.format(value) : "no data");
}
function packetStale() {
  if (lastReceived === null || invalidPacket || (!mockMode && transportState !== "open")) return true;
  return !scenarioFinished && performance.now() - lastReceived > STALE_AFTER_MS;
}
async function checkScenarioFinished() {
  healthCheckedAt = performance.now();
  const checkedFrame = frame;
  try {
    const response = await fetch(HEALTH_URL, {
      signal: AbortSignal.timeout(STALE_AFTER_MS), redirect: "error", credentials: "same-origin",
    });
    if (!response.ok) return;
    const health = await response.json();
    if (frame !== checkedFrame || transportState !== "open") return;
    const finished = health.finished === true && health.scenario === frame?.scenario;
    if (finished !== scenarioFinished) { scenarioFinished = finished; render(); }
  } catch {}
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
  const state = el("div", "reading-state");
  state.append(status, quality);
  root.append(state);
  tone(root, "unknown");
  return { root, value, unit, delta, status, quality, state };
}
function updateReading(view, reading, stale, unknownDecision = false) {
  const usable = !stale && validReading(reading);
  const state = usable && !unknownDecision ? reading.status : "unknown";
  tone(view.root, state);
  showValue(view.value, usable ? reading.value : null);
  view.root.title = usable ? `${LABELS.metric[view.root.dataset.metric]} · ${formatClock(reading.ts)}` : "";
  view.value.classList.toggle("no-value", !usable);
  setText(view.unit, usable ? reading.unit : "");
  setText(view.status, LABELS.status[state] ?? LABELS.status.unknown);
  const delta = usable && numeric(reading.baseline_delta) ? reading.baseline_delta : null;
  setText(view.delta, delta === null ? "Baseline: no data"
    : `Δ ${delta < 0 ? "−" : "+"}${readingFormat.format(Math.abs(delta))} ${reading.unit}`);
  view.delta.title = delta === null ? "Personal baseline unavailable" : `${view.delta.textContent} compared with personal baseline`;

  const quality = stale ? "stale" : reading?.quality ?? "missing";
  view.state.dataset.quality = quality;
  view.quality.hidden = quality === "ok";
  setText(view.quality, LABELS.quality[quality] ?? "missing");
}
function createCrew(index) {
  const root = el("article", "crew-card");
  const header = el("div", "crew-header");
  const identity = el("div", "crew-identity");
  const avatar = el("span", "crew-number", String(index + 1).padStart(2, "0"));
  const identityText = el("div");
  const name = el("h3", "crew-name", `Crew ${index + 1}`);
  const role = el("p", "crew-role", "Waiting for role");
  identityText.append(name, role);
  identity.append(avatar, identityText);
  const decision = el("span", "badge decision", LABELS.decision.unknown);
  const activity = el("span", "activity", "Unknown");
  header.append(identity, decision, activity);
  const deviation = el("p", "deviation", "Deviating, not yet confirmed");
  deviation.hidden = true;
  const primary = el("div", "primary-readings");
  const secondary = el("div", "secondary-readings");
  const readings = new Map();
  for (const metric of [...PRIMARY_METRICS, ...SECONDARY_METRICS]) {
    const view = createReading(metric, SECONDARY_METRICS.includes(metric), true);
    readings.set(metric, view);
    (SECONDARY_METRICS.includes(metric) ? secondary : primary).append(view.root);
  }
  const trendArea = el("div", "trend-area");
  const trendLabel = el("span", "trend-label", "HEART RATE · LAST 60 TICKS");
  const trend = document.createElementNS(SVG_NS, "svg");
  trend.setAttribute("viewBox", `0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`);
  trend.setAttribute("role", "img");
  trend.setAttribute("aria-label", "Heart rate trend: no data yet");
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
  const stamp = el("time", "reading-time", "Reading: no data");
  const details = el("button", "crew-detail-button", "View details & history ↗");
  details.type = "button";
  details.setAttribute("aria-haspopup", "dialog");
  details.setAttribute("aria-controls", "crew-detail");
  const openDetails = () => {
    const member = frame?.crew[index];
    if (member) crewDetail.open(member.id, details);
  };
  details.addEventListener("click", openDetails);
  root.addEventListener("click", event => {
    if (!event.target.closest("button, a, summary, .crew-evidence") && !window.getSelection()?.toString()) openDetails();
  });
  const actions = el("div", "crew-card-actions");
  actions.append(details, why);
  root.append(header, deviation, primary, secondary, trendArea, stamp, actions, evidence);
  tone(root, "unknown");
  return { root, name, role, decision, activity, deviation, readings, trend, stamp, evidence, why, details, evidenceBlocks: new Map() };
}
function markLabel(value) { return Number.isInteger(value * 10) ? value.toFixed(1) : value.toFixed(2); }
// marks: horizontal reference lines whose values come from alert evidence (never hard-coded here).
function drawTrend(svg, entries, unavailable, { name = "Heart-rate", marks = [] } = {}) {
  const paint = svgRenderer(svg);
  const values = unavailable ? [] : entries.filter(entry => numeric(entry.value));
  const baseline = [...entries].reverse().find(entry => numeric(entry.baseline))?.baseline ?? null;
  svg.setAttribute("aria-label", values.length
    ? `${name} trend${numeric(baseline) ? ", dashed line is the personal baseline" : ""}${marks.length ? ", dotted lines mark alert thresholds" : ""}, gaps are missing readings`
    : `${name} trend: no data`);
  if (!values.length) { paint.finish(); return; }
  const all = [...values.map(entry => entry.value), ...marks, ...(numeric(baseline) ? [baseline] : [])];
  const { minimum, maximum } = chartRange(svg, all, entries.at(-1).tick);
  const span = maximum - minimum;
  const left = TREND_LEFT;
  const right = TREND_WIDTH - TREND_RIGHT;
  const top = TREND_TOP;
  const bottom = TREND_HEIGHT - TREND_BOTTOM;
  const yOf = value => (span === 0 ? (top + bottom) / 2 : bottom - (value - minimum) / span * (bottom - top));
  paint.node("max", "text", { x: left - 3, y: top + 3, class: "trend-axis trend-axis-end" }, number(maximum));
  paint.node("min", "text", { x: left - 3, y: bottom, class: "trend-axis trend-axis-end" }, number(minimum));
  paint.node("start", "text", { x: left, y: TREND_HEIGHT - 3, class: "trend-axis" }, `−${HISTORY_TICKS}`);
  paint.node("end", "text", { x: right, y: TREND_HEIGHT - 3, class: "trend-axis trend-axis-end" }, "now");
  if (numeric(baseline)) paint.node("baseline", "line", { x1: left, x2: right, y1: yOf(baseline), y2: yOf(baseline), class: "trend-baseline" });
  marks.forEach((mark, index) => {
    paint.node(`mark:${index}`, "line", { x1: left, x2: right, y1: yOf(mark), y2: yOf(mark), class: "trend-mark" });
    paint.node(`mark-label:${index}`, "text", { x: right, y: yOf(mark) - 2, class: "trend-axis trend-axis-end trend-mark-label" }, markLabel(mark));
  });
  const lastTick = entries.at(-1).tick;
  let segment = [];
  let previous = null;
  let index = 0;
  function flush() {
    if (!segment.length) return;
    paint.node(`segment:${index}`, "polyline", { points: segment.join(" ") });
    if (segment.length === 1) {
      const [x, y] = segment[0].split(",");
      paint.node(`point:${index}`, "circle", { cx: x, cy: y, r: 2 });
    }
    index += 1;
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
  paint.finish();
}
function evidenceList(why, list = el("ul", "evidence-list")) {
  const items = why ?? [];
  while (list.children.length > items.length) list.lastElementChild.remove();
  items.forEach((item, index) => {
    let row = list.children[index];
    if (!row) {
      row = el("li");
      row.append(el("strong", "evidence-values"), el("p", "evidence-note"), el("time", "evidence-time"));
      list.append(row);
    }
    const [values, note, time] = row.children;
    setText(values, `${item.metric} · ${item.value === null ? "no data" : number(item.value)} ${item.comparator} ${item.threshold === null ? "—" : number(item.threshold)}`);
    setText(note, item.note);
    setText(time, item.ts);
    time.dateTime = item.ts;
  });
  return list;
}
function crewEvidence(view, member, alerts, stale) {
  if (stale || !alerts.length) {
    const message = stale ? "NO DATA · Waiting for current evidence." : member?.decision === "unknown"
      ? "Insufficient data to make a decision" : "No active alerts";
    if (view.evidence.firstElementChild?.className !== "empty-state") view.evidence.replaceChildren(el("p", "empty-state"));
    setText(view.evidence.firstElementChild, message);
    return;

  }
  if (view.evidence.firstElementChild?.className === "empty-state") view.evidence.firstElementChild.remove();
  const active = new Set(alerts.map(alert => alert.id));
  for (const [id, block] of view.evidenceBlocks) {
    if (!active.has(id)) { block.root.remove(); view.evidenceBlocks.delete(id); }
  }
  alerts.forEach((alert, index) => {
    let block = view.evidenceBlocks.get(alert.id);
    if (!block) {
      const root = el("section", "evidence-block");
      const badge = el("span", "badge");
      const message = el("p", "alert-message");
      const meta = el("p", "alert-meta");
      const why = evidenceList([]);
      const source = nasaData.sourceNode(alert.rule_id);
      root.append(badge, message, meta, why, source);
      block = { root, badge, message, meta, why, source, ruleId: alert.rule_id };
      view.evidenceBlocks.set(alert.id, block);
    }
    if (block.ruleId !== alert.rule_id) {
      const source = nasaData.sourceNode(alert.rule_id);
      block.source.replaceWith(source); block.source = source; block.ruleId = alert.rule_id;
    }
    tone(block.badge, alert.severity);
    setText(block.badge, LABELS.status[alert.severity]);
    setText(block.message, alert.message);
    setText(block.meta, `${alert.consecutive_count} consecutive readings`);
    evidenceList(alert.why, block.why);
    if (view.evidence.children[index] !== block.root) view.evidence.insertBefore(block.root, view.evidence.children[index] ?? null);
  });
}
function updateCrew(stale) {
  for (let index = 0; index < CREW_SIZE; index += 1) {
    const view = crewViews.get(index);
    const member = frame?.crew[index];
    const decision = stale ? "unknown" : member?.decision ?? "unknown";
    tone(view.root, DECISION_TONE[decision]);
    tone(view.decision, DECISION_TONE[decision]);
    view.name.textContent = member?.name ?? `Crew ${index + 1}`;
    view.details.disabled = !member;
    view.details.setAttribute("aria-label", `View details for ${member?.name ?? `Crew ${index + 1}`}`);

    view.role.textContent = member?.role ?? "Waiting for role";
    view.decision.textContent = stale ? "? NO DATA" : LABELS.decision[decision] ?? LABELS.decision.unknown;
    view.activity.textContent = `◌ ${LABELS.activity[stale ? "unknown" : member?.activity] ?? LABELS.activity.unknown}`;
    const deviating = !stale && decision === "normal"
      && Object.values(member?.vitals ?? {}).some(reading => reading?.status && reading.status !== "nominal");
    view.deviation.hidden = !deviating;
    view.why.setAttribute("aria-label", `${member?.name ?? `Crew ${index + 1}`}: Why?`);
    const stamped = !stale && member?.vitals?.heart_rate?.ts;
    view.stamp.textContent = stamped ? `Reading ${formatClock(stamped)} · SIMULATED` : "Reading: no data";
    if (stamped) view.stamp.dateTime = stamped; else view.stamp.removeAttribute("datetime");
    view.root.classList.toggle("stale-data", stale);
    for (const [metric, reading] of view.readings) updateReading(reading, member?.vitals[metric], stale, decision === "unknown");
    drawTrend(view.trend, histories.get(member?.id) ?? [], stale);
    crewEvidence(view, member, frame?.alerts.filter(alert => alert.crew_id === member?.id) ?? [], stale);
  }
}
function createCabinTrend(metric) {
  const root = el("div", "trend-area");
  const label = el("span", "trend-label", `${LABELS.metric[metric].toUpperCase()} · LAST ${HISTORY_TICKS} TICKS`);
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${LABELS.metric[metric]} trend: no data yet`);
  svg.classList.add("trend");
  root.append(label, svg);
  return { root, svg };
}
// Threshold lines come only from cabin alert evidence (alert.why[].threshold) of the same metric;
// no evidence seen yet means no line.
function collectThresholdMarks(alerts) {
  for (const alert of alerts) {
    if (alert.crew_id !== null) continue;
    for (const item of alert.why ?? []) {
      if (CABIN_TREND_METRICS.includes(item.metric) && numeric(item.threshold)) {
        const marks = thresholdMarks.get(item.metric) ?? new Set();
        marks.add(item.threshold);
        thresholdMarks.set(item.metric, marks);
      }
    }
  }
}
function updateCabinTrends(stale) {
  if (document.getElementById("cabin-trends").hidden) return;
  for (const [metric, view] of cabinTrendViews) {
    const marks = [...(thresholdMarks.get(metric) ?? [])].sort((a, b) => a - b);
    drawTrend(view.svg, histories.get(`cabin:${metric}`) ?? [], stale, { name: LABELS.metric[metric], marks });
  }
}
function updateCabin(stale) {
  const cabin = frame?.cabin;
  const state = stale ? "unknown" : cabin?.status ?? "unknown";
  const badge = document.getElementById("cabin-status");
  tone(badge, state);
  badge.textContent = stale ? "? NO DATA" : LABELS.status[state];
  for (const [metric, view] of cabinViews) updateReading(view, cabin?.[metric], stale);
  updateCabinTrends(stale);
  const cabinStamp = document.getElementById("cabin-time");
  const cabinTs = !stale && cabin?.pp_o2?.ts;
  cabinStamp.textContent = cabinTs ? `Reading ${formatClock(cabinTs)} · SIMULATED` : "Reading: no data";
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
    else if (!numeric(hours)) { output.textContent = "—"; note.textContent = "Not approaching the limit / unavailable"; }
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
  notice.textContent = stale ? "NO DATA · Current alert status is unknown." : "✓ No active alerts";
  container.hidden = stale;
  if (stale) return;
  const activeIds = new Set(alerts.map(alert => alert.id));
  for (const [id, view] of alertViews) {
    if (!activeIds.has(id)) { view.root.remove(); alertViews.delete(id); }
  }
  alerts.forEach((alert, index) => {
    let view = alertViews.get(alert.id);
    if (!view) {
      const root = markNew(el("details", "alert-row"));
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
    view.root.classList.toggle("new-alert", !reducedMotion.matches && performance.now() - (freshAlertIds.get(alert.id) ?? -Infinity) < ALERT_HIGHLIGHT_MS);
    view.owner.textContent = alert.crew_id === null ? "Cabin" : frame.crew.find(member => member.id === alert.crew_id)?.name ?? alert.crew_id;

    view.severity.textContent = LABELS.status[alert.severity];
    view.message.textContent = alert.message;
    view.meta.textContent = `${alert.consecutive_count} consecutive readings · Last: ${formatClock(alert.last_seen)}`;
    const key = JSON.stringify([alert.rule_id, alert.why]);
    if (key !== view.key) {
      if (!view.body.firstElementChild) view.body.append(evidenceList([]));
      evidenceList(alert.why, view.body.firstElementChild); view.key = key;
      const oldSource = view.body.querySelector(".nasa-rule-source");
      if (!oldSource || oldSource.dataset.nasaRule !== alert.rule_id) {
        const source = nasaData.sourceNode(alert.rule_id);
        if (oldSource) oldSource.replaceWith(source); else view.body.append(source);
      }
    }
    if (container.children[index] !== view.root) container.insertBefore(view.root, container.children[index] ?? null);
  });
}
// CommsMessage has no status field: delivery is derived from the frame clock (frame.ts) and deliver_at.
function deliveryState(message, now) {
  const deliverAt = Date.parse(message.deliver_at);
  if (Number.isNaN(deliverAt) || Number.isNaN(now)) return null;
  return now < deliverAt ? { delivered: false, secondsLeft: (deliverAt - now) / 1000 } : { delivered: true, secondsLeft: 0 };
}
function timeLeft(seconds) {
  const total = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(total / SECONDS_PER_MINUTE)} min ${total % SECONDS_PER_MINUTE} s left`;
}
// Messages come ordered by sent_at; a repeated id is shown once (the later copy wins).
function uniqueMessages(messages) {
  const byId = new Map();
  for (const message of messages ?? []) byId.set(message.id, message);
  return [...byId.values()].sort((a, b) => Date.parse(a.sent_at) - Date.parse(b.sent_at));
}
function createEarthRow() {
  const root = markNew(el("li", "earth-row"));
  const head = el("div", "earth-row-head");
  const sender = el("strong", "earth-sender");
  const status = el("span", "badge earth-status");
  const body = el("p", "earth-body");
  const meta = el("p", "earth-meta");
  head.append(sender, status);
  root.append(head, body, meta);
  return { root, sender, status, body, meta };
}
function updateEarthRow(view, message, now) {
  const state = deliveryState(message, now);
  tone(view.root, state?.delivered ? "nominal" : "unknown");
  tone(view.status, state?.delivered ? "nominal" : "unknown");
  view.sender.textContent = LABELS.sender[message.sender] ?? message.sender;
  view.status.textContent = state === null ? "? Unknown" : state.delivered ? "✓ Delivered" : `→ In transit · ${timeLeft(state.secondsLeft)}`;
  view.body.textContent = message.body;
  view.meta.textContent = `Sent ${formatClock(message.sent_at)} · Arrives ${formatClock(message.deliver_at)}`;
}
function updateEarth(stale) {
  if (document.getElementById("earth-messages").closest("section").hidden) return;
  const list = document.getElementById("earth-messages");
  const notice = document.getElementById("earth-notice");
  const delay = frame?.comms_one_way_delay_s;
  document.getElementById("earth-delay").textContent = stale || !numeric(delay)
    ? "Light delay: — min" : `Light delay: ${number(delay / SECONDS_PER_MINUTE)} min`;
  const messages = stale ? [] : uniqueMessages(frame.messages);
  notice.hidden = messages.length > 0;
  notice.textContent = stale ? "NO DATA · Earth link status unknown." : "No alerts sent to Earth";
  list.hidden = stale || messages.length === 0;
  const ids = new Set(messages.map(message => message.id));
  for (const [id, view] of earthViews) {
    if (!ids.has(id)) { view.root.remove(); earthViews.delete(id); }
  }
  const now = Date.parse(frame?.ts);
  messages.forEach((message, index) => {
    let view = earthViews.get(message.id);
    if (!view) { view = createEarthRow(); earthViews.set(message.id, view); }
    updateEarthRow(view, message, now);
    if (list.children[index] !== view.root) list.insertBefore(view.root, list.children[index] ?? null);
  });
}
function render() {
  const stale = packetStale();
  document.getElementById("simulated").hidden = false;
  document.getElementById("scenario").textContent = LABELS.scenario[frame?.scenario] ?? "Waiting for packets";
  document.getElementById("mission_elapsed_s").textContent = missionTime(frame?.mission_elapsed_s);
  document.getElementById("mission_elapsed_s").classList.toggle("muted", stale);
  updateCrew(stale); updateCabin(stale); updateAlerts(stale); updateEarth(stale);
  decisionBand.update(frame, stale);
  crewDetail.update(stale);
  nasaData.update(frame);
  wasStale = stale;
  updateClock();
}
const decisionBand = createDecisionBand({
  list: document.getElementById("decision-list"),
  note: document.getElementById("decisions-note"),
  helpers: {
    formatClock,
    decisionTone: decision => DECISION_TONE[decision] ?? DECISION_TONE.unknown,
    decisionLabel: decision => LABELS.decision[decision] ?? LABELS.decision.unknown,
    statusLabel: severity => LABELS.status[severity] ?? LABELS.status.unknown,
    setTone: tone,
  },
});
const finishCard = createFinishCard({
  root: document.getElementById("finish-card"),
  order: Object.keys(LABELS.scenario),
  labels: LABELS.scenario,
  start: requestScenario,
  restart: requestRestart,
  canControl: () => !controlLocked,
});
const crewDetail = createCrewDetail({
  session, labels: LABELS, format: number, evidenceList, sourceNode: ruleId => nasaData.sourceNode(ruleId),
  decisionTone: decision => DECISION_TONE[decision] ?? "unknown",
});
function updateClock() {
  const stale = packetStale();
  if (stale !== wasStale) { render(); return; }
  const seconds = lastReceived === null ? null : (performance.now() - lastReceived) / 1000;
  if (!mockMode && transportState === "open" && !scenarioFinished && seconds !== null && seconds * 1000 > HEALTH_CHECK_AFTER_MS
    && performance.now() - healthCheckedAt > HEALTH_CHECK_AFTER_MS) checkScenarioFinished();
  document.getElementById("packet-age").textContent = seconds === null ? "No packets yet" : `Last packet: ${number(Math.floor(seconds))} s ago`;
  const connection = document.getElementById("connection");
  const connected = transportState === "open" && !stale;
  const waitingSeconds = connectingSince === null ? 0 : Math.floor((performance.now() - connectingSince) / 1000);
  const waking = !mockMode && transportState !== "open" && waitingSeconds >= WAKE_AFTER_MS / 1000;
  connection.textContent = waking ? `Server is waking up… (${waitingSeconds} s)`
    : scenarioFinished && transportState === "open" ? "■ Simulation finished"
    : connected ? "● Connected" : transportState === "open" ? "◌ Waiting for telemetry" : "◌ Reconnecting";
  tone(connection, connected ? "nominal" : "unknown");
  const notice = document.getElementById("data-notice");
  const finished = scenarioFinished && !stale && !mockMode && frame !== null;
  if (finished) finishCard.show(frame.scenario); else finishCard.hide();
  notice.hidden = !stale;
  notice.textContent = invalidPacket ? "NO DATA · Could not read telemetry packet; waiting for a valid packet."
    : "NO DATA · Waiting for current telemetry. Old readings are not treated as normal.";
}
function recordHistory(key, incoming, reading, interrupted) {
  const history = histories.get(key) ?? [];
  if (interrupted && history.length) history.push({ tick: incoming.tick - 0.5, value: null, baseline: null });
  const valid = validReading(reading);
  history.push({
    tick: incoming.tick,
    value: valid ? reading.value : null,
    baseline: valid && numeric(reading.baseline_delta) ? reading.value - reading.baseline_delta : null,
  });
  histories.set(key, history.filter(entry => entry.tick > incoming.tick - HISTORY_TICKS));
}
function acceptFrame(incoming) {
  if (!validFrame(incoming)) {
    invalidPacket = true; render(); return;
  }
  const interrupted = packetStale();
  const duplicate = frame?.scenario === incoming.scenario && frame?.tick === incoming.tick && frame?.ts === incoming.ts;
  if (duplicate) {
    lastReceived = performance.now();
    invalidPacket = false;
    transportState = "open";
    if (interrupted) render();
    return;
  }
  const reset = frame?.scenario !== incoming.scenario || (previousTick !== null && incoming.tick <= previousTick);
  if (reset && frame) {
    histories.clear(); thresholdMarks.clear();
    for (const view of crewViews.values()) resetChartRange(view.trend);
    for (const view of cabinTrendViews.values()) resetChartRange(view.svg);
  }
  session.ingest(incoming, { live: true });
  const previousIds = new Set(frame?.alerts.map(alert => alert.id) ?? []);
  for (const [id, firstSeen] of freshAlertIds) {
    if (performance.now() - firstSeen >= ALERT_HIGHLIGHT_MS) freshAlertIds.delete(id);
  }
  for (const alert of incoming.alerts) {
    if (!previousIds.has(alert.id)) freshAlertIds.set(alert.id, performance.now());
  }
  for (const member of incoming.crew) recordHistory(member.id, incoming, member.vitals.heart_rate, interrupted);
  for (const metric of CABIN_TREND_METRICS) recordHistory(`cabin:${metric}`, incoming, incoming.cabin[metric], interrupted);
  collectThresholdMarks(incoming.alerts);
  frame = incoming;
  previousTick = incoming.tick;
  lastReceived = performance.now();
  scenarioFinished = false;
  invalidPacket = false;
  transportState = "open";
  connectingSince = null;
  render();
}
function currentDemoKey() {
  return document.getElementById("demo-key").value || params.get("key");
}
// True when the server guards demo controls with a key this viewer has not supplied.
let controlLocked = false;
async function probeControl() {
  // An empty body fails validation (422) after the key check, so this never changes the simulation.
  try {
    const headers = { "Content-Type": "application/json" };
    const demoKey = currentDemoKey();
    if (demoKey) headers["X-Demo-Key"] = demoKey;
    const response = await fetch(CONTROL_URL, {
      method: "POST", headers, body: "{}",
      signal: AbortSignal.timeout(STALE_AFTER_MS), redirect: "error", credentials: "same-origin",
    });
    controlLocked = response.status === 401;
  } catch { return; }
  finishCard.hide();
}
async function demoRequest(url, body, subject) {
  try {
    const headers = {};
    const demoKey = currentDemoKey();
    if (demoKey) headers["X-Demo-Key"] = demoKey;
    if (body) headers["Content-Type"] = "application/json";
    const response = await fetch(url, {
      method: "POST", headers, body,
      signal: AbortSignal.timeout(STALE_AFTER_MS), redirect: "error", credentials: "same-origin",
    });
    if (response.status === 401) return { ok: false, status: 401, message: "Demo key is required or incorrect (401). Open the page with ?demo=1 and enter the key." };
    if (!response.ok) {
      const reason = response.status === 404 ? "Endpoint or scenario not found"
        : response.status === 405 ? "Endpoint does not allow POST"
        : response.status >= 500 ? "Server error" : "Request rejected";
      return { ok: false, message: `Could not ${subject} (${response.status}): ${reason}.` };
    }
    return { ok: true, message: "Requested; waiting for telemetry." };
  } catch (error) {
    return { ok: false, message: error.name === "TimeoutError" || error.name === "AbortError"
      ? `Could not ${subject}: request timed out. The server may be waking up; try again.`
      : `Could not ${subject}: network error. Check your connection and try again.` };
  }
}
function requestScenario(scenario) {
  return demoRequest(`${SCENARIO_URL}/${encodeURIComponent(scenario)}`, null, "start scenario");
}
// Restart replays the current scenario from its first tick; the backend guards it with the same demo key.
function requestRestart() {
  return demoRequest(CONTROL_URL, JSON.stringify({ action: "restart" }), "restart scenario");
}
function configureDemo() {
  if (params.get("demo") !== "1") return;
  document.getElementById("demo-panel").hidden = false;
  const speedButtons = document.getElementById("speed-buttons");
  for (const scale of SPEED_PRESETS) {
    const button = el("button", "scenario-button speed-button", `×${scale}`);
    button.type = "button";
    button.title = `Run the simulation at ×${scale} (1 s = ${number(scale / 60)} simulated min)`;
    button.addEventListener("click", async () => {
      const result = document.getElementById("demo-result");
      button.disabled = true;
      result.textContent = "Changing speed…";
      const outcome = await demoRequest(SPEED_URL, JSON.stringify({ time_scale: scale }), "change speed");
      result.textContent = outcome.ok ? `Speed set to ×${scale}.` : outcome.message;
      if (outcome.ok) for (const other of speedButtons.children) other.setAttribute("aria-pressed", String(other === button));
      button.disabled = false;
    });
    speedButtons.append(button);
  }
  const buttons = document.getElementById("scenario-buttons");
  for (const [scenario, label] of Object.entries(LABELS.scenario)) {
    const button = el("button", "scenario-button", label);
    button.type = "button";
    button.addEventListener("click", async () => {
      const result = document.getElementById("demo-result");
      button.disabled = true;
      result.textContent = "Requesting scenario…";
      const outcome = await requestScenario(scenario);
      result.textContent = outcome.ok && mockMode
        ? "Request sent. The local mock stream continues its own sequence." : outcome.message;
      button.disabled = false;
    });
    buttons.append(button);
  }
}
async function loadRecentHistory(loader) {
  try {
    const payload = await loader();
    if (!payload || typeof payload.scenario !== "string" || !Array.isArray(payload.frames)
      || payload.frames.some(item => !validFrame(item) || item.scenario !== payload.scenario)) throw new Error("Invalid history");
    session.hydrate(payload);
    const recent = session.frames;
    for (const incoming of recent.slice(0, -1)) {
      for (const member of incoming.crew) recordHistory(member.id, incoming, member.vitals.heart_rate, false);
      for (const metric of CABIN_TREND_METRICS) recordHistory(`cabin:${metric}`, incoming, incoming.cabin[metric], false);
    }
    if (session.latest) acceptFrame(session.latest);
    crewDetail.historyStatus(recent.length ? "" : "No earlier frames available yet. New readings will appear live.");
  } catch {
    crewDetail.historyStatus("Recent history unavailable. Recording live readings from this page onward.");
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
  for (const metric of CABIN_TREND_METRICS) {
    const view = createCabinTrend(metric);
    cabinTrendViews.set(metric, view);
    document.getElementById("cabin-trends").append(view.root);
  }
  configureDemo(); render();
  if (!mockMode) {
    probeControl();
    let probeTimer = null;
    document.getElementById("demo-key").addEventListener("input", () => {
      window.clearTimeout(probeTimer);
      probeTimer = window.setTimeout(probeControl, 400);
    });
  }
  clockTimer = window.setInterval(updateClock, CLOCK_INTERVAL_MS);
  if (mockMode) {
    document.getElementById("source-label").textContent = "LOCAL MOCK · Decisions are fixed demo examples";
    try {
      const { startMock, mockHistory } = await import("./mock.js");
      await loadRecentHistory(() => mockHistory(120));
      stopMock = startMock(acceptFrame);
    } catch { transportState = "closed"; invalidPacket = true; render(); }
  } else {
    await loadRecentHistory(async () => {
      const response = await fetch("/api/history?limit=120", {
        signal: AbortSignal.timeout(STALE_AFTER_MS), redirect: "error", credentials: "same-origin",
      });
      if (!response.ok) throw new Error("History unavailable");
      return response.json();
    });
    connectStream();
  }
}
function retryStream() {
  if (pageHidden || retryTimer !== null) return;
  retryTimer = window.setTimeout(() => { retryTimer = null; connectStream(); }, RECONNECT_AFTER_MS);
}
function connectStream() {
  if (pageHidden) return;
  stream?.close();
  transportState = "connecting";
  connectingSince ??= performance.now();
  try {
    stream = new EventSource(STREAM_URL);
    stream.addEventListener("open", () => {
      transportState = "open"; connectingSince = null; updateClock();
    });
    stream.addEventListener(TELEMETRY_EVENT, event => {
      try { acceptFrame(JSON.parse(event.data)); }
      catch { invalidPacket = true; render(); }
    });
    stream.addEventListener("end", () => {
      scenarioFinished = true;
      render();
    });
    stream.addEventListener("error", () => {
      transportState = "connecting";
      connectingSince ??= performance.now();
      scenarioFinished = false;
      if (stream.readyState === EventSource.CLOSED) retryStream();
      render();
    });
  } catch {
    transportState = "closed";
    retryStream();
    updateClock();
  }
}
window.addEventListener("pagehide", () => {
  pageHidden = true;
  stream?.close(); stopMock?.();
  window.clearInterval(clockTimer); window.clearTimeout(retryTimer);
});
window.addEventListener("pageshow", event => { if (event.persisted) window.location.reload(); });
start();
