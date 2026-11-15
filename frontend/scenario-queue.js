/** Finish card: after a scenario ends, count down to the next one and offer two shortcuts.
 *
 * The card never talks to the network itself. `start(scenario)` is injected and resolves to
 * `{ ok, message }`, so the demo key, error wording and request code stay in one place (app.js).
 */

export const NEXT_SCENARIO_SECONDS = 10;
const TICK_MS = 250;

export function nextScenario(order, current) {
  const index = order.indexOf(current);
  return order[(index + 1) % order.length];
}

export function createFinishCard({ root, order, labels, start, seconds = NEXT_SCENARIO_SECONDS }) {
  const spinner = root.querySelector(".finish-spinner");
  const title = root.querySelector(".finish-title");
  const detail = root.querySelector(".finish-detail");
  const skip = root.querySelector(".finish-skip");
  const replay = root.querySelector(".finish-replay");
  let timer = null;
  let current = null;
  let next = null;
  let deadline = 0;
  let pausedAt = null;
  let busy = false;

  const label = scenario => labels[scenario] ?? scenario;
  const remaining = () => Math.max(0, Math.ceil(((pausedAt ?? performance.now()) - deadline) / -1000));

  function setBusy(value) {
    busy = value;
    skip.disabled = value;
    replay.disabled = value;
    root.dataset.state = value ? "starting" : timer === null ? "idle" : "counting";
  }
  function renderCountdown() {
    const left = remaining();
    detail.textContent = `Next scenario: ${label(next)} · starts in ${left} s`;
    root.style.setProperty("--finish-progress", String(1 - left / seconds));
  }
  function stopTimer() {
    window.clearInterval(timer);
    timer = null;
  }
  async function go(scenario) {
    if (busy) return;
    stopTimer();
    setBusy(true);
    title.textContent = `Starting ${label(scenario)}…`;
    detail.textContent = "Waiting for the first telemetry packet.";
    const result = await start(scenario);
    if (result.ok) return;
    // Failure: stay on the card, keep the buttons usable, and do not retry automatically.
    setBusy(false);
    root.dataset.state = "error";
    title.textContent = "Could not start the scenario";
    detail.textContent = result.message;
  }
  function tick() {
    if (document.hidden) { pausedAt ??= performance.now(); return; }
    if (pausedAt !== null) { deadline += performance.now() - pausedAt; pausedAt = null; }
    if (remaining() <= 0) { go(next); return; }
    renderCountdown();
  }

  skip.addEventListener("click", () => go(next));
  replay.addEventListener("click", () => go(current));
  document.addEventListener("visibilitychange", () => { if (!document.hidden && timer !== null) tick(); });

  return {
    /** Idempotent: calling it every clock tick keeps the same countdown running. */
    show(scenario) {
      if (!root.hidden && current === scenario) return;
      current = scenario;
      next = nextScenario(order, scenario);
      root.hidden = false;
      title.textContent = "Simulation finished";
      replay.textContent = `Replay ${label(scenario)}`;
      skip.textContent = `Skip to ${label(next)}`;
      spinner.hidden = false;
      setBusy(false);
      pausedAt = null;
      deadline = performance.now() + seconds * 1000;
      stopTimer();
      timer = window.setInterval(tick, TICK_MS);
      root.dataset.state = "counting";
      renderCountdown();
    },
    hide() {
      if (root.hidden) return;
      stopTimer();
      root.hidden = true;
      current = null;
      busy = false;
    },
  };
}
