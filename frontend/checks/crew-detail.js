import { createSession, metricSeries, chartEvents, sessionCsv, CHART_FRAME_LIMIT } from "../session.js";
import { drawDetailChart, createCrewDetail } from "../crew-detail.js";
import { setText, svgRenderer, chartRange, resetChartRange } from "../ui.js";
import { mockHistory } from "../mock.js";

const results = [];
const assert = (value, message) => { if (!value) throw new Error(message); };
function check(name, run) {
  try { run(); results.push({ name, ok: true }); }
  catch (error) { results.push({ name, ok: false, message: error.message }); }
}
const epoch = Date.parse("2026-01-01T00:00:00Z");
function frame(tick, scenario = "nominal", reading = {}) {
  const ts = new Date(epoch + tick * 1000).toISOString();
  return {
    contract_version: "0.2.0", scenario, tick, ts, mission_elapsed_s: tick,
    crew: Array.from({ length: 4 }, (_, i) => ({
      id: `crew-${i + 1}`, name: `Person ${i + 1}`, activity: "resting", decision: "normal",
      vitals: Object.fromEntries(["heart_rate", "spo2", "resp_rate", "core_temp", "hrv_rmssd", "systolic_bp", "diastolic_bp"].map(metric =>
        [metric, { value: 80, baseline_delta: 10, status: "nominal", quality: "ok", unit: "bpm", ts, ...reading }])),
    })), cabin: {}, alerts: [],
  };
}
function alert(tick, crewId = "crew-1") {
  const ts = new Date(epoch + tick * 1000).toISOString();
  return { id: "alert-1", crew_id: crewId, first_seen: ts, last_seen: ts, message: "Test alert", severity: "caution", why: [] };
}
function chart(frames) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  drawDetailChart(svg, metricSeries(frames, "crew-1", "heart_rate"), chartEvents(frames, "crew-1"), String);
  return svg;
}
check("Unchanged text preserves its existing text node", () => {
  const node = document.createElement("span"); node.textContent = "Stable";
  const text = node.firstChild; setText(node, "Stable");
  assert(node.firstChild === text, "Unchanged text was recreated");
});
check("SVG updates preserve existing paths and remove obsolete markers", () => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  let paint = svgRenderer(svg);
  const path = paint.node("path", "polyline", { points: "0,0 1,1" });
  paint.node("old-event", "line", { x1: 0, x2: 0 }); paint.finish();
  paint = svgRenderer(svg);
  const next = paint.node("path", "polyline", { points: "0,1 1,2" }); paint.finish();
  assert(path === next && svg.children.length === 1 && path.getAttribute("points") === "0,1 1,2", "SVG churn or obsolete marker");
});
check("Chart ranges stay stable for noise and reset for a new session", () => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  chartRange(svg, [60, 100], 0);
  const stable = chartRange(svg, [70, 80], 1);
  assert(stable.minimum === 60 && stable.maximum === 100, "Axis shrank with every sample");
  resetChartRange(svg);
  const reset = chartRange(svg, [70, 80], 0);
  assert(reset.minimum === 70 && reset.maximum === 80, "New person/session kept the old range");
});
check("Detail charts reuse their measurement path when telemetry changes", () => {
  const svg = chart([frame(0), frame(1)]);
  const path = svg.querySelector(".detail-measurement");
  drawDetailChart(svg, metricSeries([frame(0), frame(1), frame(2)], "crew-1", "heart_rate"), [], String);
  assert(svg.querySelector(".detail-measurement") === path, "Live update replaced the SVG path");
});
check("Late join loads ordered history and deduplicates the first SSE frame", () => {
  const session = createSession();
  assert(session.hydrate({ scenario: "nominal", frames: [frame(2), frame(0), frame(1)] }), "History not loaded");
  session.ingest(frame(2), { live: true });
  session.ingest(frame(3), { live: true });
  assert(session.frames.length === 4 && session.archive.length === 4, "Duplicate or lost frame");
});
check("Invalid history frames never enter the session", () => {
  const session = createSession();
  session.hydrate({ scenario: "nominal", frames: [frame(0), { ...frame(1), ts: "bad" }, frame(2, "hypoxia")] });
  assert(session.frames.length === 1, "Invalid history accepted");
});
check("Scenario changes clear charts and retain all earlier CSV frames", () => {
  const session = createSession();
  session.ingest(frame(4), { live: true });
  session.ingest(frame(0, "hypoxia"), { live: true });
  assert(session.frames.length === 1 && session.archive.length === 2 && session.scenario === "hypoxia", "Wrong reset");
  assert([...sessionCsv(session.archive)].join("").includes('"nominal"'), "Earlier session missing");
});
check("Same-scenario restart creates a separate CSV segment", () => {
  const session = createSession();
  session.ingest(frame(4), { live: true });
  session.ingest(frame(0), { live: true });
  assert(session.frames.length === 1 && session.archive[1].segment !== session.archive[0].segment, "Restart not separated");
});
check("Chart window is bounded while CSV retains the complete session", () => {
  const session = createSession();
  for (let tick = 0; tick < 150; tick += 1) session.ingest(frame(tick), { live: true });
  assert(session.chartFrames.length === CHART_FRAME_LIMIT && session.archive.length === 150, "Wrong limits");
  assert([...sessionCsv(session.archive)].length === 1 + 150 * 4 * 7, "CSV truncated");
});
check("Personal baseline is calculated separately for every frame", () => {
  const points = metricSeries([frame(0), frame(1, "nominal", { value: 100, baseline_delta: 20 })], "crew-1", "heart_rate");
  assert(points[0].baseline === 70 && points[1].baseline === 80, "Baseline flattened");
});
check("Missing, stale and faulty readings become chart gaps", () => {
  for (const reading of [{ value: null, quality: "missing" }, { quality: "stale" }, { quality: "sensor_fault" }]) {
    const points = metricSeries([frame(0, "nominal", reading)], "crew-1", "heart_rate");
    assert(points[0].value === null && points[0].baseline === null && points[0].status === "unknown", "Invalid reading plotted");
  }
});
check("SVG never bridges missing readings or unreceived ticks", () => {
  const missing = chart([frame(0), frame(1, "nominal", { value: null, quality: "missing" }), frame(2)]);
  const skipped = chart([frame(0), frame(3)]);
  assert(missing.querySelectorAll(".detail-measurement").length === 2, "Missing data connected");
  assert(skipped.querySelectorAll(".detail-measurement").length === 2, "Unreceived data connected");
});
check("Unavailable baseline does not create an invented reference", () => {
  const svg = chart([frame(0, "nominal", { baseline_delta: null }), frame(1, "nominal", { baseline_delta: null })]);
  assert(svg.querySelectorAll(".detail-baseline").length === 0, "Baseline invented");
});
check("Activity changes and alert first_seen are distinct markers for this person", () => {
  const frames = [frame(0), frame(1), frame(2)];
  frames[1].crew[0].activity = "exercise";
  frames[2].crew[0].activity = "exercise";
  frames[1].alerts = [alert(1), alert(1, "crew-2")];
  frames[2].alerts = [alert(1)];
  const events = chartEvents(frames, "crew-1");
  assert(events.length === 2 && events.every(event => event.time === epoch + 1000), "Wrong or duplicate event");
  const svg = chart(frames);
  assert(svg.querySelectorAll(".detail-marker-alert").length === 1 && svg.querySelectorAll(".detail-marker-activity").length === 1, "Missing marker");
});
check("Time axis uses timestamps instead of equally spaced sample indices", () => {
  const frames = [frame(0), frame(1), frame(2)];
  frames[2].ts = new Date(epoch + 10000).toISOString();
  const points = chart(frames).querySelector(".detail-measurement").getAttribute("points").split(" ").map(pair => Number(pair.split(",")[0]));
  assert(Math.abs((points[1] - points[0]) / (points[2] - points[0]) - .1) < .001, "Index axis used");
});
check("Cross-midnight axes identify both UTC dates", () => {
  const frames = [frame(0), frame(1)];
  frames[0].ts = "2026-01-01T23:59:00Z"; frames[1].ts = "2026-01-02T00:00:00Z";
  const text = chart(frames).textContent;
  assert(text.includes("01-01 23:59:00") && text.includes("01-02 00:00:00"), "Date rollover is ambiguous");
});
check("Earlier alert onsets do not appear as new chart-window events", () => {
  const current = frame(100); current.alerts = [alert(1)];
  assert(chartEvents([current], "crew-1").length === 0, "Old alert onset included in current window");
});
check("Latest alert survives clearing but resets with the scenario", () => {
  const session = createSession();
  const initial = frame(0); initial.alerts = [alert(0)];
  session.ingest(initial, { live: true }); session.ingest(frame(1), { live: true });
  assert(session.latestAlert("crew-1")?.id === "alert-1", "Last alert lost");
  session.ingest(frame(0, "hypoxia"), { live: true });
  assert(session.latestAlert("crew-1") === null, "Old scenario evidence retained");
});
check("CSV preserves quoted names, numeric deltas and raw invalid-reading quality", () => {
  const session = createSession();
  const incoming = frame(0, "nominal", { value: null, baseline_delta: null, quality: "sensor_fault" });
  incoming.crew[0].name = 'A, "quoted" name';
  incoming.crew[1].vitals.heart_rate = { ...incoming.crew[1].vitals.heart_rate, value: 60, baseline_delta: -10, quality: "ok" };
  session.ingest(incoming);
  const csv = [...sessionCsv(session.archive)].join("");
  assert(csv.includes('"A, ""quoted"" name"') && csv.includes('"unknown","sensor_fault"') && csv.includes('"-10"'), "CSV corruption");
});
check("CSV text cells cannot execute spreadsheet formulas", () => {
  const session = createSession(); const incoming = frame(0); incoming.crew[0].name = "=1+1";
  session.ingest(incoming);
  assert([...sessionCsv(session.archive)].join("").includes("'=1+1"), "Formula not neutralized");
});
check("Mock history matches the API envelope and contains one current scenario", () => {
  const payload = mockHistory(3);
  assert(payload.frames.length === 3 && payload.frames.every(frame => frame.scenario === payload.scenario), "Invalid mock envelope");
});
try {
  const sandbox = document.createElement("div");
  sandbox.innerHTML = `<button id="test-trigger">Open test detail</button><dialog id="crew-detail"><h2 id="detail-title"></h2><p id="detail-subtitle"></p><span id="detail-decision"></span><div id="detail-charts"></div><div id="detail-evidence"></div><ol id="detail-events"></ol><p id="detail-notice"></p><p id="detail-count"></p><button id="download-session">Download</button><button id="detail-close">Close</button></dialog>`;
  document.body.append(sandbox);
  const session = createSession(); session.ingest(frame(0), { live: true });
  const detail = createCrewDetail({ session, labels: { metric: {}, activity: { resting: "Resting" }, decision: { normal: "Normal", unknown: "Unknown" }, status: { nominal: "Normal" }, quality: {} }, format: String, evidenceList: () => document.createElement("ul"), decisionTone: () => "nominal" });
  assert(document.querySelectorAll(".detail-chart").length === 0, "Charts built before first open");
  detail.update(false); detail.open("crew-1", document.getElementById("test-trigger"));
  assert(document.querySelectorAll(".detail-chart").length === 7, "Not seven charts");
  detail.update(true);
  detail.open("crew-1", document.getElementById("test-trigger"));
  assert(document.getElementById("detail-decision").dataset.tone === "unknown", "Stale decision not unknown");
  assert(document.querySelectorAll(".detail-measurement").length === 7, "Historical readings hidden on outage");
  document.getElementById("detail-close").click();
  await new Promise(resolve => setTimeout(resolve, 0));
  const before = document.getElementById("detail-count").textContent;
  session.ingest(frame(1), { live: true }); detail.update(false);
  await new Promise(resolve => setTimeout(resolve, 30));
  assert(document.getElementById("detail-count").textContent === before, "Closed panel rendered");
  assert(document.activeElement.id === "test-trigger", "Focus not returned");
  sandbox.remove();
  results.push({ name: "Dialog builds lazily, keeps outage history, restores focus and skips hidden updates", ok: true });
} catch (error) { results.push({ name: "Dialog lifecycle and hidden rendering", ok: false, message: error.message }); }
const benchmarkFrames = Array.from({ length: 120 }, (_, tick) => frame(tick, "nominal", { value: 80 + tick % 8 }));
const benchmarkStart = performance.now();
for (let run = 0; run < 20; run += 1) for (let metric = 0; metric < 7; metric += 1) chart(benchmarkFrames);
const benchmark = document.createElement("p");
benchmark.id = "benchmark";
benchmark.textContent = `SVG benchmark: 20 updates × 7 charts × 120 frames in ${(performance.now() - benchmarkStart).toFixed(1)} ms (local browser; indicative only).`;
document.body.append(benchmark);
const list = document.getElementById("checks");
for (const result of results) {
  const row = document.createElement("li");
  row.textContent = `${result.ok ? "PASS" : "FAIL"} · ${result.name}${result.message ? `: ${result.message}` : ""}`;
  list.append(row);
}
const failures = results.filter(result => !result.ok).length;
document.getElementById("result").textContent = `${results.length - failures}/${results.length} crew detail checks passed`;
document.body.dataset.failures = String(failures);
