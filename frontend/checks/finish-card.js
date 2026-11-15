import { createFinishCard, nextScenario } from "../scenario-queue.js";
const results = [];
const assert = (value, message) => { if (!value) throw new Error(message); };
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function check(name, test) {
  try { await test(); results.push({ name, ok: true }); }
  catch (error) { results.push({ name: `${name}: ${error.message}`, ok: false }); }
}
const order = ["a", "b", "c"];
const labels = { a: "Alpha", b: "Beta", c: "Gamma" };
const root = document.getElementById("finish-card");
function make(start, seconds = 1) { return createFinishCard({ root, order, labels, start, seconds }); }
await check("Next scenario wraps around the fixed order", () => {
  assert(nextScenario(order, "a") === "b" && nextScenario(order, "c") === "a", "Wrong order");
});
await check("Card shows next scenario, countdown and two buttons", () => {
  const card = make(async () => ({ ok: true }), 30);
  card.show("a");
  assert(!root.hidden && root.querySelector(".finish-detail").textContent.includes("Beta") && root.querySelector(".finish-detail").textContent.includes("30 s"), "Countdown text");
  assert(root.querySelector(".finish-skip").textContent.includes("Beta") && root.querySelector(".finish-replay").textContent.includes("Alpha"), "Button labels");
  card.hide();
  assert(root.hidden, "Card did not hide");
});
await check("Skip starts the next scenario, Replay starts the current one", async () => {
  const started = [];
  const card = make(async scenario => { started.push(scenario); return { ok: true }; }, 30);
  card.show("a"); root.querySelector(".finish-skip").click(); await wait(20); card.hide();
  card.show("a"); root.querySelector(".finish-replay").click(); await wait(20); card.hide();
  assert(started.join() === "b,a", `Started ${started.join()}`);
});
await check("Countdown starts the next scenario by itself exactly once", async () => {
  const started = [];
  const card = make(async scenario => { started.push(scenario); return { ok: true }; }, 1);
  card.show("c"); await wait(1700); card.hide();
  assert(started.join() === "a", `Started ${started.join()}`);
});
await check("A failed start stays on the card and does not retry", async () => {
  let calls = 0;
  const card = make(async () => { calls += 1; return { ok: false, message: "Demo key is required" }; }, 1);
  card.show("a"); await wait(1700);
  assert(calls === 1 && root.dataset.state === "error" && root.querySelector(".finish-detail").textContent.includes("Demo key"), "Error handling");
  assert(!root.querySelector(".finish-skip").disabled, "Buttons stay usable");
  card.hide();
});
const failed = results.filter(item => !item.ok);
document.getElementById("result").textContent = failed.length ? `${failed.length} finish card checks failed` : `${results.length}/${results.length} finish card checks passed`;
document.getElementById("checks").replaceChildren(...results.map(item => Object.assign(document.createElement("li"), { textContent: `${item.ok ? "PASS" : "FAIL"} · ${item.name}` })));
