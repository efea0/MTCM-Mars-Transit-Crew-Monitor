import { setText, svgRenderer, chartRange, resetChartRange, closeOnBackdrop } from "./ui.js";
import { VITAL_METRICS, finite, usable, metricSeries, chartEvents, sessionCsv } from "./session.js";

const NS = "http://www.w3.org/2000/svg";
const WIDTH = 560;
const HEIGHT = 142;
const LEFT = 48;
const RIGHT = WIDTH - 12;
const TOP = 18;
const BOTTOM = HEIGHT - 26;

function element(tag, className, text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
function svgNode(tag, attributes, text = "") {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  node.textContent = text;
  return node;
}
const clock = time => new Date(time).toISOString().slice(5, 19).replace("T", " ");

export function drawDetailChart(svg, series, events, format) {
  const paint = svgRenderer(svg);
  const values = series.flatMap(point => [point.value, point.baseline]).filter(finite);
  if (!values.length) {
    paint.node("empty", "text", { x: LEFT, y: HEIGHT / 2, class: "detail-axis" }, "No valid readings in this window");
    paint.finish();
    return;
  }
  const { minimum, maximum } = chartRange(svg, values, series.at(-1).tick);
  const firstTime = series[0].time;
  const lastTime = series.at(-1).time;
  const x = time => firstTime === lastTime ? (LEFT + RIGHT) / 2 : LEFT + (time - firstTime) / (lastTime - firstTime) * (RIGHT - LEFT);
  const y = value => maximum === minimum ? (TOP + BOTTOM) / 2 : BOTTOM - (value - minimum) / (maximum - minimum) * (BOTTOM - TOP);
  [...new Set([minimum, maximum])].forEach((value, index) => {
    paint.node(`grid:${index}`, "line", { x1: LEFT, x2: RIGHT, y1: y(value), y2: y(value), class: "detail-gridline" });
    paint.node(`axis:${index}`, "text", { x: LEFT - 6, y: y(value) + 4, "text-anchor": "end", class: "detail-axis" }, format(value));
  });
  paint.node("start", "text", { x: LEFT, y: HEIGHT - 6, class: "detail-axis" }, `${clock(firstTime)} UTC`);
  paint.node("end", "text", { x: RIGHT, y: HEIGHT - 6, "text-anchor": "end", class: "detail-axis" }, `${clock(lastTime)} UTC`);

  function segments(field) {
    const parts = [];
    let current = [];
    let previous = null;
    for (const point of series) {
      if (!finite(point[field]) || (previous && (point.tick !== previous.tick + 1 || point.time <= previous.time))) {
        if (current.length) parts.push(current);
        current = [];
      }
      if (finite(point[field])) current.push(point);
      previous = point;
    }
    if (current.length) parts.push(current);
    return parts;
  }
  segments("baseline").forEach((part, index) => {
    const points = part.map(point => `${x(point.time)},${y(point.baseline)}`).join(" ");
    paint.node(`baseline-band:${index}`, "polyline", { points, class: "detail-baseline-band" });
    paint.node(`baseline:${index}`, "polyline", { points, class: "detail-baseline" });
  });
  for (const event of events.filter(item => item.time >= firstTime && item.time <= lastTime)) {
    const line = paint.node(`event:${event.kind}:${event.time}:${event.label}`, "line", {
      x1: x(event.time), x2: x(event.time), y1: TOP, y2: BOTTOM,
      class: `detail-marker detail-marker-${event.kind}`, "data-kind": event.kind,
    });
    if (!line.firstElementChild) line.append(svgNode("title", {}));
    setText(line.firstElementChild, `${clock(event.time)} UTC · ${event.label}`);
  }
  segments("value").forEach((part, index) => {
    paint.node(`measurement:${index}`, "polyline", { points: part.map(point => `${x(point.time)},${y(point.value)}`).join(" "), class: "detail-measurement" });
    if (part.length === 1) paint.node(`point:${index}`, "circle", { cx: x(part[0].time), cy: y(part[0].value), r: 2.5, class: "detail-point" });
  });
  paint.finish();
}

export function createCrewDetail({ session, labels, format, evidenceList, decisionTone, sourceNode = () => null }) {
  const dialog = document.getElementById("crew-detail");
  const title = document.getElementById("detail-title");
  const subtitle = document.getElementById("detail-subtitle");
  const badge = document.getElementById("detail-decision");
  const grid = document.getElementById("detail-charts");
  const evidence = document.getElementById("detail-evidence");
  const markers = document.getElementById("detail-events");
  const notice = document.getElementById("detail-notice");
  const count = document.getElementById("detail-count");
  const download = document.getElementById("download-session");
  const views = new Map();
  const eventViews = new Map();
  const evidenceLabel = element("p", "detail-kicker");
  const evidenceMessage = element("p", "detail-alert-message");
  const evidenceRows = evidenceList([]);
  const evidenceSource = element("div", "detail-source");
  let sourceRule = null;
  evidence.append(evidenceLabel, evidenceMessage, evidenceRows, evidenceSource);
  let seriesKey = null;
  let crewId = null;
  let revision = -1;
  let oldStale = null;
  let animation = null;
  let returnFocus = null;
  let unavailable = true;
  let historyNotice = "Loading recent history…";

  function build() {
    if (views.size) return;
    for (const metric of VITAL_METRICS) {
      const card = element("section", "detail-chart-card");
      card.dataset.metric = metric;
      const heading = element("div", "detail-chart-heading");
      const name = element("h3", "", labels.metric[metric]);
      const value = element("strong", "detail-chart-value", "no data");
      const status = element("span", "detail-chart-status");
      const svg = svgNode("svg", { viewBox: `0 0 ${WIDTH} ${HEIGHT}`, role: "img", "aria-label": `${labels.metric[metric]} over time; dashed personal baseline; gaps mean missing readings` });
      svg.classList.add("detail-chart");
      heading.append(name, value);
      card.append(heading, status, svg);
      grid.append(card);
      views.set(metric, { card, value, status, svg });
    }
  }

  function render() {
    animation = null;
    if (!dialog.open || (revision === session.revision && oldStale === unavailable)) return;
    revision = session.revision;
    oldStale = unavailable;
    const frames = session.chartFrames;
    const latest = session.latest;
    const member = latest?.crew.find(person => person.id === crewId);
    setText(title, member?.name ?? "Crew member unavailable");
    setText(subtitle, `${member?.role ?? "Unknown role"} · ${labels.activity[member?.activity] ?? "Unknown activity"}`);
    badge.dataset.tone = unavailable ? "unknown" : decisionTone(member?.decision);
    badge.textContent = unavailable ? "? Unknown · no current data" : labels.decision[member?.decision] ?? labels.decision.unknown;
    notice.textContent = unavailable
      ? "NO CURRENT DATA · Historical readings remain visible. The current decision is unknown."
      : historyNotice;
    notice.hidden = !notice.textContent;
    count.textContent = `${frames.length} chart frames · ${session.archive.length} session frames · SIMULATED DATA`;
    download.disabled = session.archive.length === 0;
    const events = chartEvents(frames, crewId);
    const nextSeriesKey = `${session.scenario}:${crewId}`;
    if (seriesKey !== nextSeriesKey) {
      for (const view of views.values()) resetChartRange(view.svg);
      seriesKey = nextSeriesKey;
    }
    for (const [metric, view] of views) {
      const reading = member?.vitals[metric];
      const valid = !unavailable && usable(reading);
      view.card.dataset.tone = valid ? reading.status : "unknown";
      setText(view.value, valid ? `${format(reading.value)} ${reading.unit ?? ""}` : "no data");
      view.status.textContent = valid ? labels.status[reading.status] ?? labels.status.unknown
        : `Unknown · ${unavailable ? "current data unavailable" : labels.quality[reading?.quality] ?? "missing"}`;
      drawDetailChart(view.svg, metricSeries(frames, crewId, metric), events, format);
    }
    const lastAlert = session.latestAlert(crewId);
    if (lastAlert) {
      const active = latest.alerts.some(alert => alert.id === lastAlert.id && alert.crew_id === crewId);
      const state = unavailable ? "Last recorded alert · current state unknown" : active ? "Latest active alert" : "Last recorded alert · cleared";
      setText(evidenceLabel, state);
      setText(evidenceMessage, lastAlert.message);
      evidenceMessage.hidden = false;
      evidenceRows.hidden = false;
      evidenceList(lastAlert.why, evidenceRows);
      evidenceSource.hidden = false;
      if (sourceRule !== lastAlert.rule_id) {
        sourceRule = lastAlert.rule_id;
        const source = sourceNode(sourceRule);
        evidenceSource.replaceChildren(...(source ? [source] : []));
      }
    } else {
      setText(evidenceLabel, member?.decision === "unknown" || unavailable
        ? "Insufficient data for a current decision. No recorded alert for this person." : "No recorded alerts for this person.");
      evidenceMessage.hidden = true;
      evidenceRows.hidden = true;
      evidenceSource.hidden = true;
    }
    const recent = events.slice(-8).reverse();
    const keys = new Set(recent.map(event => `${event.kind}:${event.time}:${event.label}`));
    for (const [key, view] of eventViews) {
      if (!keys.has(key)) { view.root.remove(); eventViews.delete(key); }
    }
    if (markers.firstElementChild?.className === "empty-state") markers.firstElementChild.remove();
    recent.forEach((event, index) => {
      const key = `${event.kind}:${event.time}:${event.label}`;
      let view = eventViews.get(key);
      if (!view) {
        const root = element("li", `detail-event detail-event-${event.kind}`);
        const time = element("time", "");
        const text = element("span", "");
        root.append(time, text);
        view = { root, time, text };
        eventViews.set(key, view);
      }
      setText(view.time, `${clock(event.time)} UTC`);
      setText(view.text, event.label);
      if (markers.children[index] !== view.root) markers.insertBefore(view.root, markers.children[index] ?? null);
    });
    if (!recent.length) markers.append(element("li", "empty-state", "No activity changes or alert onsets in this window."));
  }
  function schedule() {
    if (dialog.open && animation === null) animation = requestAnimationFrame(render);
  }
  function open(id, trigger) {
    crewId = id;
    returnFocus = trigger;
    revision = -1;
    build();
    if (!dialog.open) dialog.showModal();
    document.body.classList.add("detail-open");
    render();
    document.getElementById("detail-close").focus();
  }
  document.getElementById("detail-close").addEventListener("click", () => dialog.close());
  closeOnBackdrop(dialog);
  dialog.addEventListener("close", () => {
    document.body.classList.remove("detail-open");
    if (animation !== null) cancelAnimationFrame(animation);
    animation = null;
    returnFocus?.focus({ preventScroll: true });
  });
  download.addEventListener("click", () => {
    const blob = new Blob(["\ufeff", ...sessionCsv(session.archive)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "mars-transit-session.csv";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  return {
    open,
    update(stale) { unavailable = stale; schedule(); },
    historyStatus(text) { historyNotice = text; revision = -1; schedule(); },
  };
}
