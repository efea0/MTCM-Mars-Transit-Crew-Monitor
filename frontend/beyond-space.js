// "Beyond space": the same design applied to other sealed, air-limited environments.
// Information only. It never reads telemetry, never calls the API and never changes what the live
// dashboard shows (the live engine runs the Mars transit profile only). The numbers below mirror
// data/reference/ranges.json (profiller); tests/test_frontend_beyond_space.py keeps them in sync.
export const PROFILES = [
  {
    id: "mars_transit", name: "Mars transit vehicle", volume: "75 m³", crew: 4,
    co2: "3.0 mmHg", o2: "PIO₂ ≥ 127 mmHg", engine: "Live",
    source: "NASA-STD-3001 [V2 6004], Table 6.2-1",
    note: "The only profile the live dashboard runs today.",
  },
  {
    id: "denizalti", name: "Submarine", volume: "2000 m³", crew: 100,
    co2: "0.8 % (6.1 mmHg)", o2: "Not modeled", engine: "Planned",
    source: "NRC 2007, Table S-1 (90-day recommendation); PubMed 30511437 (2018)",
    note: "0.8 % is an NRC recommendation for 90-day exposure, not an official Navy limit. Crew metabolic rates are an astronaut average (assumption).",
  },
  {
    id: "yeralti_siginagi", name: "Underground refuge chamber (US coal mine)", volume: "30 m³", crew: 10,
    co2: "1.0 % average, up to 2.5 % (7.6 mmHg average)", o2: "18.5 – 23.0 %", engine: "Planned",
    source: "30 CFR 7.506",
    note: "96-hour supply target. CO and methane are not modeled.",
  },
];
const SOURCES = [
  ["NASA-STD-3001 Vol 2 Rev F", null],
  ["NRC 2007, Table S-1: Emergency and Continuous Exposure Guidance Levels for Selected Submarine Contaminants", "https://www.nap.edu/read/11170/chapter/2"],
  ["Submarine CO₂ exposure guideline study (2018), PubMed 30511437", "https://pubmed.ncbi.nlm.nih.gov/30511437/"],
  ["30 CFR 7.506, refuge alternative breathable air (eCFR)", "https://www.ecfr.gov/current/title-30/section-7.506"],
];
const STORY = [
  "People already live and work in sealed, air-limited places on Earth, such as submarines and underground refuge chambers. The same questions apply there: how long is the air safe, who needs to act, and what should they do?",
  "On the Mars transit vehicle, the astronaut monitor answers them with a cabin air budget (O₂ and CO₂ mass balance) and decisions read against each person's own baseline. The design is meant to carry over to other environments by changing only the profile: volume, crew size, equipment capacity and limits.",
  "Underground exploration, such as caving, was part of the inspiration. Caves are not modeled here: they are not sealed environments and we have no sourced limits for them. Limits are prepared for the two sealed environments in the table below.",
];

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}
function cell(tag, text, className = "") { return node(tag, className, text); }
function profileTable() {
  const table = node("table", "nasa-table beyond-table");
  table.append(node("caption", "", "Environment profiles prepared in data/reference/ranges.json"));
  const head = node("tr");
  for (const label of ["Environment", "Volume", "Crew", "CO₂ limit", "O₂ limit", "Limit source", "Engine"]) {
    const th = cell("th", label); th.scope = "col"; head.append(th);
  }
  const thead = node("thead");
  thead.append(head);
  table.append(thead);
  const body = node("tbody");
  for (const profile of PROFILES) {
    const row = node("tr");
    const name = cell("th", ""); name.scope = "row";
    name.append(node("strong", "", profile.name), node("small", "nasa-muted", profile.note));
    row.append(name, cell("td", profile.volume), cell("td", String(profile.crew)), cell("td", profile.co2, "nasa-limit-value"),
      cell("td", profile.o2, "nasa-limit-value"), cell("td", profile.source, "nasa-source-text"),
      cell("td", profile.engine === "Live" ? "● Live" : "○ Planned"));
    body.append(row);
  }
  table.append(body);
  return table;
}
export function createBeyondSpace({ trigger }) {
  const dialog = node("dialog", "nasa-dialog beyond-dialog");
  dialog.id = "beyond-space";
  dialog.setAttribute("aria-labelledby", "beyond-title");
  dialog.setAttribute("aria-describedby", "beyond-disclaimer");
  const head = node("header", "nasa-header");
  const heading = node("div");
  heading.append(node("p", "nasa-kicker", "SAME DESIGN · OTHER SEALED ENVIRONMENTS"));
  const title = node("h2", "", "Beyond space"); title.id = "beyond-title";
  heading.append(title);
  const close = node("button", "nasa-button", "Close"); close.type = "button";
  head.append(heading, close);
  const disclaimer = node("p", "nasa-disclaimer", "Limits and sources are prepared as data. Engine support and an environment selector are planned, not built: the live dashboard runs the Mars transit profile only. Volumes, crew sizes and equipment capacities are demo assumptions; only the limits are sourced.");
  disclaimer.id = "beyond-disclaimer";
  const content = node("div", "nasa-content");
  const why = node("section", "nasa-section");
  why.append(node("h3", "", "Why this matters on Earth"));
  for (const line of STORY) why.append(node("p", "beyond-story", line));
  const profiles = node("section", "nasa-section");
  profiles.append(node("h3", "", "Environment profiles"));
  const wrap = node("div", "nasa-table-wrap"); wrap.tabIndex = 0;
  wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", "Environment profiles table, scrolls sideways on small screens");
  wrap.append(profileTable());
  profiles.append(wrap);
  const limits = node("section", "nasa-section");
  limits.append(node("h3", "", "What this view does not do"));
  const list = node("ul", "beyond-list");
  for (const text of [
    "It does not change the live dashboard, its decisions or its countdowns.",
    "It does not claim the engine already runs these environments: that support is planned.",
    "Submarine and refuge-chamber numbers are not medical thresholds; this is decision support, not diagnosis.",
  ]) list.append(node("li", "", text));
  limits.append(list);
  const sources = node("section", "nasa-section");
  sources.append(node("h3", "", "Sources"));
  const sourceList = node("ul", "beyond-list");
  for (const [label, url] of SOURCES) {
    const item = node("li");
    if (url) {
      const anchor = node("a", "", label);
      anchor.href = url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
      item.append(anchor);
    } else item.textContent = label;
    sourceList.append(item);
  }
  sources.append(sourceList);
  content.append(why, profiles, limits, sources);
  dialog.append(head, disclaimer, content);
  document.body.append(dialog);
  trigger.setAttribute("aria-haspopup", "dialog");
  trigger.setAttribute("aria-controls", dialog.id);
  let lastFocus = null;
  trigger.addEventListener("click", () => {
    lastFocus = document.activeElement;
    if (!dialog.open) dialog.showModal();
    dialog.scrollTop = 0;
  });
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => lastFocus?.isConnected && lastFocus.focus({ preventScroll: true }));
  return { dialog };
}
