const rows = document.getElementById("checks");
const stage = document.getElementById("stage");
const results = [];
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const assert = (value, message) => { if (!value) throw new Error(message); };
async function ready(frame) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (frame.contentDocument?.querySelector(".crew-detail-button:not(:disabled)")) {
      await frame.contentDocument.fonts.ready;
      return;
    }
    await wait(100);
  }
  throw new Error("Frame did not receive mock telemetry");
}
function controls(document) {
  return [...document.querySelectorAll(".crew-card-actions button")].map(node => {
    const box = node.getBoundingClientRect();
    return { node, x: box.x, y: box.y, width: box.width, height: box.height };
  });
}
for (const [width, height] of [[1920, 1080], [1366, 768], [640, 480], [400, 300]]) {
  const frame = document.createElement("iframe");
  frame.width = String(width); frame.height = String(height); frame.title = `${width} × ${height} layout`;
  frame.src = "/?mock=1";
  stage.replaceChildren(frame);
  try {
    await ready(frame);
    const doc = frame.contentDocument;
    const win = frame.contentWindow;
    const before = controls(doc);
    const earth = doc.querySelector(".earth-panel");
    assert(earth.checkVisibility(), "Earth link panel is hidden");
    assert(doc.getElementById("earth-delay").textContent === "Light delay: 12 min", "Earth delay did not render");
    const firstMessage = doc.querySelector(".earth-row");
    assert(firstMessage?.querySelector(".earth-status").textContent.includes("In transit"), "Mock message is not in transit");
    assert(win.innerWidth === width && win.innerHeight === height, "Incorrect test viewport");
    for (let sample = 0; sample < 30; sample += 1) {
      await wait(500);
      const now = controls(doc);
      now.forEach((box, index) => {
        const old = before[index];
        assert(old.node === box.node, "Control was replaced during telemetry");
        assert(Math.abs(old.x - box.x) < .5 && Math.abs(old.y - box.y) < .5
          && Math.abs(old.width - box.width) < .5 && Math.abs(old.height - box.height) < .5,
        `Control shifted at ${width} × ${height}: ${old.y} → ${box.y}`);
      });
      assert(doc.documentElement.scrollWidth <= width, "Horizontal page overflow");
    }
    assert(earth.checkVisibility(), "Earth link panel became hidden");
    assert(firstMessage.isConnected && firstMessage.querySelector(".earth-status").textContent.includes("Delivered"),
      "Earth message did not transition to delivered with live telemetry");
    if (width >= 1366) assert(doc.documentElement.scrollHeight <= height, `Overview exceeds viewport: ${doc.documentElement.scrollHeight} > ${height}`);
    win.scrollTo(0, 500);
    await wait(50);
    const topbar = doc.querySelector(".topbar").getBoundingClientRect();
    assert(win.scrollY === 0 || topbar.bottom <= 0, "Header covers the scrolled readings");
    results.push({ ok: true, name: `${width} × ${height}: visible Earth link and live delivery, stable controls, no horizontal overflow, no header overlap` });
  } catch (error) { results.push({ ok: false, name: `${width} × ${height}: ${error.message}` }); }
  const row = document.createElement("li"); const result = results.at(-1);
  row.textContent = `${result.ok ? "PASS" : "FAIL"} · ${result.name}`; rows.append(row);
}
const failures = results.filter(result => !result.ok).length;
document.getElementById("result").textContent = `${results.length - failures}/${results.length} layout checks passed`;
document.body.dataset.failures = String(failures);
