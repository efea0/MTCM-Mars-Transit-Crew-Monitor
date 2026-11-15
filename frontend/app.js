// main.py ile uyumlandırılacak taslak API adresleri ve SSE olay adı.
const STREAM_URL = "/api/stream";
const SCENARIO_URL = "/api/scenario";
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
const TREND_HEIGHT = 32;
const TREND_PADDING = 3;
const ALERT_HIGHLIGHT_MS = 1600;
const LABELS = {
  metric: {
    heart_rate: "Nabız", spo2: "SpO₂", resp_rate: "Solunum", core_temp: "Çekirdek ısı",
    hrv_rmssd: "HRV · RMSSD", systolic_bp: "Sistolik KB", diastolic_bp: "Diyastolik KB",
    pp_o2: "O₂ kısmi basınç", pp_co2: "CO₂ kısmi basınç", cabin_pressure: "Kabin basıncı",
    cabin_temp: "Kabin sıcaklığı", rel_humidity: "Bağıl nem", radiation_dose_rate: "Radyasyon hızı",
    pio2: "Solunan O₂ basıncı", event_dose: "Olay dozu",
  },
  status: { nominal: "● Normal", caution: "△ Dikkat", warning: "▲ Uyarı", critical: "! Kritik", unknown: "? Bilinmiyor" },
  decision: { normal: "✓ Normal", watch: "◉ İzle", action: "! Eylem", unknown: "? Bilinmiyor" },
  activity: { resting: "Dinleniyor", exercise: "Egzersiz", sleep: "Uyku", unknown: "Bilinmiyor" },
  quality: { ok: "Geçerli", stale: "eski", missing: "yok", sensor_fault: "sensör hatası" },
  scenario: { nominal: "Normal seyir", hypoxia: "O₂ arızası", hypercapnia: "CO₂ yükselişi", solar_storm: "Güneş fırtınası", sensor_dropout: "Veri kopması" },
};
const PRIMARY_METRICS = ["heart_rate", "spo2", "resp_rate", "core_temp"];
const SECONDARY_METRICS = ["hrv_rmssd", "systolic_bp", "diastolic_bp"];
const CABIN_METRICS = ["pp_o2", "pp_co2", "cabin_pressure", "cabin_temp", "rel_humidity", "radiation_dose_rate"];
const DECISION_TONE = { normal: "nominal", watch: "caution", action: "critical", unknown: "unknown" };
const SEVERITY_ORDER = { critical: 3, warning: 2, caution: 1 };
const numberFormat = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 });
const params = new URLSearchParams(window.location.search);
const mockMode = params.get("mock") === "1";
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
function packetStale() {
  return lastReceived === null || invalidPacket || performance.now() - lastReceived > STALE_AFTER_MS;
}
function validReading(reading) { return reading && numeric(reading.value) && reading.quality === "ok"; }
function missionTime(seconds) {
  if (!numeric(seconds)) return "— g — sa — dk";
  const totalMinutes = Math.floor(seconds / SECONDS_PER_MINUTE);
  const totalHours = Math.floor(totalMinutes / MINUTES_PER_HOUR);
  return `${Math.floor(totalHours / HOURS_PER_DAY)} g ${totalHours % HOURS_PER_DAY} sa ${totalMinutes % MINUTES_PER_HOUR} dk`;
}
function createReading(metric, secondary = false, baseline = false) {
  const root = el("div", `reading${secondary ? " secondary" : ""}`);
  root.dataset.metric = metric;
  const label = el("span", "reading-label", LABELS.metric[metric]);
  const output = el("div", "reading-output");
  const value = el("strong", "reading-value", "veri yok");
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
  view.value.textContent = usable ? number(reading.value) : "veri yok";
  view.value.classList.toggle("no-value", !usable);
  view.unit.textContent = usable ? reading.unit : "";
  view.status.textContent = LABELS.status[state] ?? LABELS.status.unknown;
  const delta = usable && numeric(reading.baseline_delta) ? reading.baseline_delta : null;
  view.delta.textContent = delta === null ? "Baz çizgiye göre: —"
    : `${delta < 0 ? "−" : "+"}${number(Math.abs(delta))} ${reading.unit} baz çizgiye göre`;
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
  const name = el("h3", "crew-name", `Mürettebat ${index + 1}`);
  const role = el("p", "crew-role", "Rol bekleniyor");
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
  const trendLabel = el("span", "trend-label", "NABIZ · SON 60 TICK");
  const trend = document.createElementNS(SVG_NS, "svg");
  trend.setAttribute("viewBox", `0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`);
  trend.setAttribute("role", "img");
  trend.setAttribute("aria-label", "Nabız trendi: henüz veri yok");
  trend.classList.add("trend");
  trendArea.append(trendLabel, trend);
  const why = el("button", "why-button", "Neden? ↗");
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
  root.append(header, primary, secondary, trendArea, why, evidence);
  tone(root, "unknown");
  return { root, name, role, decision, activity, readings, trend, evidence, why, evidenceKey: null };
}
function drawTrend(svg, entries, unavailable) {
  svg.replaceChildren();
  const values = unavailable ? [] : entries.filter(entry => numeric(entry.value));
  svg.setAttribute("aria-label", values.length ? "Nabız trendi, boşluklar eksik ölçümdür" : "Nabız trendi: veri yok");
  if (!values.length) return;
  const minimum = Math.min(...values.map(entry => entry.value));
  const maximum = Math.max(...values.map(entry => entry.value));
  const span = maximum - minimum;
  const lastTick = entries.at(-1).tick;
  let segment = [];
  let previous = null;
  function flush() {
    if (!segment.length) return;
    const line = document.createElementNS(SVG_NS, "polyline");
    line.setAttribute("points", segment.join(" "));
    svg.append(line);
    if (segment.length === 1) {
      const dot = document.createElementNS(SVG_NS, "circle");
      const [x, y] = segment[0].split(",");
      dot.setAttribute("cx", x);
      dot.setAttribute("cy", y);
      dot.setAttribute("r", "2");
      svg.append(dot);
    }
    segment = [];
  }
  for (const entry of entries) {
    if (!numeric(entry.value) || (previous !== null && entry.tick !== previous + 1)) flush();
    if (numeric(entry.value)) {
      const x = TREND_PADDING + (entry.tick - lastTick + HISTORY_TICKS - 1)
        / (HISTORY_TICKS - 1) * (TREND_WIDTH - TREND_PADDING * 2);
      const y = span === 0 ? TREND_HEIGHT / 2 : TREND_HEIGHT - TREND_PADDING
        - (entry.value - minimum) / span * (TREND_HEIGHT - TREND_PADDING * 2);
      segment.push(`${x.toFixed(2)},${y.toFixed(2)}`);
    }
    previous = entry.tick;
  }
  flush();
}
function evidenceList(why) {
  const list = el("ul", "evidence-list");
  for (const item of why ?? []) {
    const row = el("li");
    const values = `${item.metric} · ${item.value === null ? "veri yok" : number(item.value)} ${item.comparator} ${item.threshold === null ? "—" : number(item.threshold)}`;
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
    view.evidence.append(el("p", "empty-state", "VERİ YOK · Güncel kanıt bekleniyor."));
  } else if (!alerts.length) {
    view.evidence.append(el("p", "empty-state", member.decision === "unknown"
      ? "Veri yetersiz, karar verilemiyor" : "Aktif uyarı yok"));
  } else {
    for (const alert of alerts) {
      const block = el("section", "evidence-block");
      const badge = el("span", "badge", LABELS.status[alert.severity]);
      tone(badge, alert.severity);
      block.append(badge, el("p", "alert-message", alert.message),
        el("p", "alert-meta", `${alert.consecutive_count} art arda ölçüm`), evidenceList(alert.why));
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
    view.name.textContent = member?.name ?? `Mürettebat ${index + 1}`;
    view.role.textContent = member?.role ?? "Rol bekleniyor";
    view.decision.textContent = stale ? "? VERİ YOK" : LABELS.decision[decision] ?? LABELS.decision.unknown;
    view.activity.textContent = `◌ ${LABELS.activity[stale ? "unknown" : member?.activity] ?? LABELS.activity.unknown}`;
    view.why.setAttribute("aria-label", `${member?.name ?? `Mürettebat ${index + 1}`}: Neden?`);
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
  badge.textContent = stale ? "? VERİ YOK" : LABELS.status[state];
  for (const [metric, view] of cabinViews) updateReading(view, cabin?.[metric], stale);
  for (const field of ["hours_until_o2_limit", "hours_until_co2_limit"]) {
    const root = document.getElementById(field);
    const hours = cabin?.[field];
    const output = root.querySelector("strong");
    const note = root.querySelector("small");
    tone(root, stale || !numeric(hours) ? "unknown" : hours === 0 ? "critical" : state);
    root.classList.toggle("neutral-countdown", !stale && !numeric(hours));
    if (stale) { output.textContent = "VERİ YOK"; note.textContent = "Güncel telemetri bekleniyor"; }
    else if (!numeric(hours)) { output.textContent = "—"; note.textContent = "Limite yaklaşmıyor / hesaplanamıyor"; }
    else if (hours === 0) { output.textContent = "LİMİT AŞILDI"; note.textContent = "Simülasyon süresi"; }
    else {
      const minutes = Math.round(hours * MINUTES_PER_HOUR);
      output.textContent = `${Math.floor(minutes / MINUTES_PER_HOUR)} sa ${minutes % MINUTES_PER_HOUR} dk`;
      note.textContent = "Simülasyon süresi";
    }
  }
}
function updateAlerts(stale) {
  const container = document.getElementById("alerts");
  const notice = document.getElementById("alerts-notice");
  const alerts = stale ? [] : [...frame.alerts].sort((a, b) =>
    (SEVERITY_ORDER[b.severity] ?? 0) - (SEVERITY_ORDER[a.severity] ?? 0)
    || Date.parse(b.last_seen) - Date.parse(a.last_seen));
  document.getElementById("alert-count").textContent = stale ? "—" : `${alerts.length} AKTİF`;
  notice.hidden = alerts.length > 0;
  notice.textContent = stale ? "VERİ YOK · Güncel uyarı durumu bilinmiyor." : "✓ Aktif uyarı yok";
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
    view.owner.textContent = alert.crew_id === null ? "Kabin" : frame.crew.find(member => member.id === alert.crew_id)?.name ?? alert.crew_id;
    view.severity.textContent = LABELS.status[alert.severity];
    view.message.textContent = alert.message;
    view.meta.textContent = `${alert.consecutive_count} art arda ölçüm · Son: ${alert.last_seen}`;
    const key = JSON.stringify(alert.why);
    if (key !== view.key) { view.body.replaceChildren(evidenceList(alert.why)); view.key = key; }
    if (container.children[index] !== view.root) container.insertBefore(view.root, container.children[index] ?? null);
  });
}
function render() {
  const stale = packetStale();
  document.getElementById("simulated").hidden = frame?.simulated === false;
  document.getElementById("scenario").textContent = LABELS.scenario[frame?.scenario] ?? "Paket bekleniyor";
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
  document.getElementById("packet-age").textContent = seconds === null ? "Henüz paket yok" : `Son paket: ${number(Math.floor(seconds))} sn önce`;
  const connection = document.getElementById("connection");
  const connected = transportState === "open" && !stale;
  connection.textContent = connected ? "● Bağlı" : transportState === "closed" ? "× Koptu" : "◌ Yeniden bağlanıyor";
  tone(connection, connected ? "nominal" : "unknown");
  const notice = document.getElementById("data-notice");
  notice.hidden = !stale;
  notice.textContent = invalidPacket ? "VERİ YOK · Telemetri paketi okunamadı; geçerli paket bekleniyor."
    : "VERİ YOK · Güncel telemetri bekleniyor. Eski ölçümler normal kabul edilmez.";
}
function acceptFrame(incoming) {
  // Tam şema backend'e aittir; bozuk veya başka sürümde paket normal görünemez.
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
    if (interrupted && history.length) history.push({ tick: incoming.tick - 0.5, value: null });
    const reading = member.vitals.heart_rate;
    history.push({ tick: incoming.tick, value: validReading(reading) ? reading.value : null });
    histories.set(member.id, history.filter(entry => entry.tick > incoming.tick - HISTORY_TICKS));
  }
  frame = incoming;
  previousTick = incoming.tick;
  lastReceived = performance.now();
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
      result.textContent = "Senaryo isteniyor…";
      try {
        const headers = {};
        const demoKey = document.getElementById("demo-key").value;
        if (demoKey) headers["X-Demo-Key"] = demoKey;
        const response = await fetch(`${SCENARIO_URL}/${encodeURIComponent(scenario)}`, {
          method: "POST", headers,
          signal: AbortSignal.timeout(STALE_AFTER_MS), redirect: "error", credentials: "same-origin",
        });
        if (response.status === 401) { result.textContent = "Demo anahtarı gerekli veya yanlış."; return; }
        if (!response.ok) throw new Error("scenario unavailable");
        result.textContent = mockMode ? "İstek gönderildi. Yerel mock akışı kendi sırasıyla sürer." : "Senaryo istendi; telemetri bekleniyor.";
      } catch { result.textContent = "backend henüz hazır değil"; }
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
  configureDemo(); render();
  clockTimer = window.setInterval(updateClock, CLOCK_INTERVAL_MS);
  if (mockMode) {
    document.getElementById("source-label").textContent = "YEREL MOCK · Kararlar sabit demo örnekleridir";
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
