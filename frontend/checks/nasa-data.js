import { createNasaData, validateLimits, validateSep, linkedTimeline, safeNasaUrl, findLimit, fetchNasaResponse } from "../nasa-data.js";
import { mockNasaResponse } from "../mock.js";
const results = [];
const assert = (value, message) => { if (!value) throw new Error(message); };
async function check(name, test) {
  try { await test(); results.push({ name, ok: true }); }
  catch (error) { results.push({ name: `${name}: ${error.message}`, ok: false }); }
}
const fixture = mockNasaResponse("/api/limits");
const event = mockNasaResponse("/api/sep-event");
await check("Mock endpoints retain the real envelopes", () => {
  assert(validateLimits(fixture).items.length === 4, "Missing fixture rows");
  assert(validateSep(event).linked_events.length === 2, "Missing linked records");
  const history = mockNasaResponse("/api/history?limit=12");
  assert(history.frames.length <= 12 && history.frames.every(frame => frame.scenario === history.scenario), "History shape/scenario mismatch");
  assert(history.frames.every((frame,index) => !index || frame.tick === history.frames[index-1].tick + 1), "History is not contiguous");
});
await check("Malformed limits fail instead of inventing normal values", () => {
  for (const payload of [null, {}, { items: [null] }, { items: [...fixture.items, fixture.items[0]] }]) {
    let rejected = false; try { validateLimits(payload); } catch { rejected = true; } assert(rejected, "Bad payload accepted");
  }
});
await check("Null thresholds remain unavailable", () => {
  const copy = structuredClone(fixture); copy.items[0].threshold = null; validateLimits(copy);
  assert(copy.items[0].threshold === null, "Missing threshold changed");
});
await check("Only exact rule IDs match; no guessed thresholds", () => {
  assert(findLimit(fixture, "pio2-hypoxia-low")?.source === fixture.items[0].source, "Exact match failed");
  assert(findLimit(fixture, "pio2-hypoxia") === null && findLimit(fixture, "mock-rule") === null, "Guessed match");
});
await check("Only HTTPS NASA event links are clickable", () => {
  assert(safeNasaUrl(event.link), "Real NASA link rejected");
  for (const value of ["javascript:alert(1)", "https://nasa.gov.evil.test/", "http://nasa.gov/", "https://user:pass@nasa.gov/"]) assert(!safeNasaUrl(value), "Unsafe link accepted");
});
await check("Chronology retains all linked records plus SEP", () => {
  const items = linkedTimeline({ ...event, linked_events: [...event.linked_events].reverse() });
  assert(items.length === 3 && items[0].type === "FLR" && items[2].type === "SEP", "Timeline order/loss");
});
await check("Explicit mock data access works without a server response", async () => {
  assert((await fetchNasaResponse("/api/limits", true)).version === "LOCAL MOCK", "Mock not selected");
});
const trigger = document.getElementById("trigger");
const banner = document.getElementById("banner");
const calls = [];
let failSep = true;
let emptySep = false;
const view = createNasaData({ trigger, banner, mock: true, load: async path => {
  calls.push(path);
  if (path === "/api/sep-event" && failSep) throw new Error("Network down");
  return path === "/api/limits" ? structuredClone(fixture) : { ...structuredClone(event), linked_events: emptySep ? [] : event.linked_events };
} });
await view.ready();
await check("Reference catalogue loads only once", async () => { await view.ready(); await view.ready(); assert(calls.filter(path => path === "/api/limits").length === 1, "Repeated requests"); });
trigger.focus(); trigger.click();
await new Promise(resolve => setTimeout(resolve, 20));
const dialog = document.getElementById("nasa-data");
await check("Always-available trigger opens accessible native dialog", () => {
  assert(dialog.open && dialog.getAttribute("aria-labelledby") === "nasa-title", "Dialog not open/labelled");
  assert(document.getElementById("nasa-disclaimer").textContent.includes("LOCAL MOCK"), "Fixtures passed off as live NASA data");
});
await check("Three groups and all verification states are explicit", () => {
  assert(dialog.querySelectorAll(".nasa-limit-group").length === 3, "Missing group");
  for (const status of ["verified", "partial", "unverified"]) assert(dialog.querySelector(`.nasa-verification.${status}`), `Missing ${status}`);
  assert(dialog.querySelector(".nasa-demo-warning").textContent.includes("DEMO PARAMETERS"), "Vital demo parameters hidden");
});
await check("Table uses supplied source, units and thresholds", () => {
  const row = [...dialog.querySelectorAll("tr[data-rule-id]")].find(row => row.dataset.ruleId === "pio2-hypoxia-low");
  assert(row.textContent.includes("127 mmHg") && row.textContent.includes(fixture.items[0].source), "Source/threshold not from response");
});
await check("Failed DONKI request offers retry without fabricated timeline", () => {
  assert(dialog.querySelector(".nasa-event").childElementCount === 0, "Fake event rendered");
  assert([...dialog.querySelectorAll("button")].some(button => button.textContent === "Retry DONKI event" && !button.hidden), "No retry");
});
failSep = false;
[...dialog.querySelectorAll("button")].find(button => button.textContent === "Retry DONKI event").click();
await new Promise(resolve => setTimeout(resolve,20));
await check("Retry recovers event with independent metadata and safe links", () => {
  assert(dialog.querySelectorAll(".nasa-stage").length === 3, "No recovered timeline");
  const anchor = dialog.querySelector(".nasa-event a");
  assert(anchor.target === "_blank" && anchor.rel.includes("noopener"), "External link unsafe");
  assert(dialog.querySelector(".nasa-event-note").textContent.includes("Dose is simulated"), "Dose attribution missing");
});
await check("Source evidence links exact reference row", () => {
  const source = view.sourceNode("pio2-hypoxia-low"); document.body.append(source);
  source.querySelector("button").click();
  assert(dialog.querySelector(".nasa-selected")?.dataset.ruleId === "pio2-hypoxia-low", "Incorrect reference row");
  assert(source.textContent.includes(fixture.items[0].source), "Missing source evidence");
  source.remove();
});
await check("Unmatched rule remains explicitly unmatched", () => { assert(view.sourceNode("pio2-not-real").textContent.includes("not matched"), "Unknown rule pretended verified"); });
await check("Solar banner retains geometry across scenario updates", () => {
  view.update({ scenario: "nominal" }); const before = banner.getBoundingClientRect();
  assert(banner.tabIndex === -1 && banner.getAttribute("aria-hidden") === "true", "Inactive banner focusable");
  view.update({ scenario: "solar_storm" }); const after = banner.getBoundingClientRect();
  assert(before.height === after.height && before.width === after.width, "Banner moved layout");
  assert(banner.textContent.includes("Dose simulated") && banner.tabIndex === 0, "Missing solar provenance");
  view.update({ scenario: "nominal" });
});
await check("Escape/close restore focus without scrolling", () => {
  document.querySelector('[aria-label="Close NASA data and limits"]').click();
  assert(!dialog.open, "Dialog did not close");
});
await new Promise(resolve => setTimeout(resolve,20));
await check("Reopening uses cached responses", async () => {
  const before = calls.length; trigger.click(); await new Promise(resolve => setTimeout(resolve,20));
  assert(calls.length === before, "Repeated reference requests on open");
});
dialog.close();
await new Promise(resolve => setTimeout(resolve,20));
await check("Focus returns to overview trigger", () => { assert(document.activeElement === trigger, "Focus not restored"); });
for (const result of results) { const row = document.createElement("li"); row.textContent = `${result.ok ? "PASS" : "FAIL"} · ${result.name}`; document.getElementById("checks").append(row); }
const failed = results.filter(result => !result.ok).length;
document.getElementById("result").textContent = `${results.length-failed}/${results.length} NASA data checks passed`;
document.body.dataset.failures = String(failed);
