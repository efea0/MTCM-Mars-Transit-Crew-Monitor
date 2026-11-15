// Restart control: replays the current scenario from the start via POST /api/control {"action": "restart"}.
// The backend protects this endpoint with the demo key (X-Demo-Key) when DEMO_CONTROL_KEY is set.
const RESTART_BODY = JSON.stringify({ action: "restart" });

export async function requestRestart(url, demoKey, timeoutMs) {
  const headers = { "Content-Type": "application/json" };
  if (demoKey) headers["X-Demo-Key"] = demoKey;
  try {
    const response = await fetch(url, {
      method: "POST", headers, body: RESTART_BODY,
      signal: AbortSignal.timeout(timeoutMs), redirect: "error", credentials: "same-origin",
    });
    if (response.status === 401) return "Demo key is required or incorrect (401). Open the page with ?demo=1 and enter it.";
    if (!response.ok) return `Could not restart (${response.status}).`;
    return "Restart requested; waiting for telemetry.";
  } catch (error) {
    return error.name === "TimeoutError" || error.name === "AbortError"
      ? "Could not restart: request timed out. Try again."
      : "Could not restart: network error. Check your connection and try again.";
  }
}
