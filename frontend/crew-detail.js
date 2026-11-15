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
const clock = time => new Date(time).toISOString().slice(11, 19);
const displayUnit = unit => ({ "nefes/dk": "breaths/min", "µGy/saat": "µGy/h" })[unit] ?? unit ?? "";

export function drawDetailChart(svg, series, events, format) {
  svg.replaceChildren();
  const values = series.flatMap(point => [point.value, point.baseline]).filter(finite);
  if (!values.length) {
    svg.append(svgNode("text", { x: LEFT, y: HEIGHT / 2, class: "detail-axis" }, "No valid readings in this window"));
    return;
  }
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const firstTime = series[0].time;
  const lastTime = series.at(-1).time;
  const x = time => firstTime === lastTime ? (LEFT + RIGHT) / 2 : LEFT + (time - firstTime) / (lastTime - firstTime) * (RIGHT - LEFT);
  const y = value => maximum === minimum ? (TOP + BOTTOM) / 2 : BOTTOM - (value - minimum) / (maximum - minimum) * (BOTTOM - TOP);
  for (const value of new Set([minimum, maximum])) {
    svg.append(svgNode("line", { x1: LEFT, x2: RIGHT, y1: y(value), y2: y(value), class: "detail-gridline" }),
      svgNode("text", { x: LEFT - 6, y: y(value) + 4, "text-anchor": "end", class: "detail-axis" }, format(value)));
  }
  svg.append(svgNode("text", { x: LEFT, y: HEIGHT - 6, class: "detail-axis" }, `${clock(firstTime)} UTC`),
    svgNode("text", { x: RIGHT, y: HEIGHT - 6, "text-anchor": "end", class: "detail-axis" }, `${clock(lastTime)} UTC`));

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
  for (const part of segments("baseline")) {
    const points = part.map(point => `${x(point.time)},${y(point.baseline)}`).join(" ");
    svg.append(svgNode("polyline", { points, class: "detail-baseline-band" }),
      svgNode("polyline", { points, class: "detail-baseline" }));
  }
  for (const event of events.filter(item => item.time >= firstTime && item.time <= lastTime)) {
    const line = svgNode("line", { x1: x(event.time), x2: x(event.time), y1: TOP, y2: BOTTOM,
      class: `detail-marker detail-marker-${event.kind}`, "data-kind": event.kind });
    line.append(svgNode("title", {}, `${clock(event.time)} UTC · ${event.label}`));
    svg.append(line);
  }
  for (const part of segments("value")) {
    svg.append(svgNode("polyline", { points: part.map(point => `${x(point.time)},${y(point.value)}`).join(" "), class: "detail-measurement" }));
    if (part.length === 1) svg.append(svgNode("circle", { cx: x(part[0].time), cy: y(part[0].value), r: 2.5, class: "detail-point" }));
  }
}

export function createCrewDetail({ session, labels, format, evidenceList, decisionTone }) {
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
    title.textContent = member?.name ?? "Crew member unavailable";
    subtitle.textContent = `${member?.role ?? "Unknown role"} · ${labels.activity[member?.activity] ?? "Unknown activity"}`;
    badge.dataset.tone = unavailable ? "unknown" : decisionTone(member?.decision);
    badge.textContent = unavailable ? "? Unknown · no current data" : labels.decision[member?.decision] ?? labels.decision.unknown;
    notice.textContent = unavailable
      ? "NO CURRENT DATA · Historical readings remain visible. The current decision is unknown."
      : historyNotice;
    notice.hidden = !notice.textContent;
    count.textContent = `${frames.length} chart frames · ${session.archive.length} session frames · SIMULATED DATA`;
    download.disabled = session.archive.length === 0;
    const events = chartEvents(frames, crewId);
    for (const [metric, view] of views) {
      const reading = member?.vitals[metric];
      const valid = !unavailable && usable(reading);
      view.card.dataset.tone = valid ? reading.status : "unknown";
      view.value.textContent = valid ? `${format(reading.value)} ${displayUnit(reading.unit)}` : "no data";
      view.status.textContent = valid ? labels.status[reading.status] ?? labels.status.unknown
        : `Unknown · ${unavailable ? "current data unavailable" : labels.quality[reading?.quality] ?? "missing"}`;
      drawDetailChart(view.svg, metricSeries(frames, crewId, metric), events, format);
    }
    const lastAlert = session.latestAlert(crewId);
    evidence.replaceChildren();
    if (lastAlert) {
      const active = latest.alerts.some(alert => alert.id === lastAlert.id && alert.crew_id === crewId);
      const state = unavailable ? "Last recorded alert · current state unknown" : active ? "Latest active alert" : "Last recorded alert · cleared";
      evidence.append(element("p", "detail-kicker", state), element("p", "detail-alert-message", lastAlert.message), evidenceList(lastAlert.why));
    } else evidence.append(element("p", "empty-state", member?.decision === "unknown" || unavailable
      ? "Insufficient data for a current decision. No recorded alert for this person." : "No recorded alerts for this person."));
    const recent = events.slice(-8).reverse();
    markers.replaceChildren(...recent.map(event => {
      const row = element("li", `detail-event detail-event-${event.kind}`);
      row.append(element("time", "", `${clock(event.time)} UTC`), element("span", "", event.label));
      return row;
    }));
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
  dialog.addEventListener("close", () => {
    document.body.classList.remove("detail-open");
    if (animation !== null) cancelAnimationFrame(animation);
    animation = null;
    returnFocus?.focus();
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
