import { setText } from "./ui.js";

/** Decision-first band: every non-normal decision and cabin alert in one row above the fold.
 *
 * Data comes from the telemetry frame only; nothing here invents thresholds or text. The whole alert
 * sentence (which already carries the recommendation) and up to three evidence notes are shown as-is.
 */

const MAX_EVIDENCE_NOTES = 3;
const SEVERITY_RANK = { critical: 4, warning: 3, caution: 2, unknown: 1 };

function rank(severity) {
  return SEVERITY_RANK[severity] ?? 0;
}

function heaviest(alerts) {
  return [...alerts].sort((a, b) =>
    rank(b.severity) - rank(a.severity) || Date.parse(b.last_seen) - Date.parse(a.last_seen))[0] ?? null;
}

function notesOf(alert) {
  return (alert?.why ?? []).map(item => item.note).filter(Boolean).slice(0, MAX_EVIDENCE_NOTES);
}

export function createDecisionBand({ list, note, helpers }) {
  const views = new Map();
  let emptyView = null;

  function itemForCrew(member, alerts) {
    const alert = heaviest(alerts);
    const decision = member.decision ?? "unknown";
    return {
      key: `crew:${member.id}`,
      owner: member.name ?? member.id,
      tone: alert ? alert.severity : helpers.decisionTone(decision),
      badge: helpers.decisionLabel(decision),
      message: alert
        ? alert.message
        : decision === "unknown"
          ? "Insufficient data to decide; missing readings are not treated as normal."
          : `Decision: ${helpers.decisionLabel(decision)}. Open Why? on the crew card for evidence.`,
      meta: alert ? `${alert.consecutive_count} consecutive readings · Last ${helpers.formatClock(alert.last_seen)}` : "",
      notes: notesOf(alert),
    };
  }

  function itemForCabin(alert) {
    return {
      key: `cabin:${alert.id}`,
      owner: "Cabin",
      tone: alert.severity,
      badge: helpers.statusLabel(alert.severity),
      message: alert.message,
      meta: `${alert.consecutive_count} consecutive readings · Last ${helpers.formatClock(alert.last_seen)}`,
      notes: notesOf(alert),
    };
  }

  function collect(frame) {
    const alerts = frame?.alerts ?? [];
    const crew = (frame?.crew ?? []).filter(Boolean);
    const items = [];
    for (const member of crew) {
      const own = alerts.filter(alert => alert.crew_id === member.id);
      const attention = member.decision === "watch" || member.decision === "action";
      const unknown = member.decision === "unknown";
      if (attention || unknown || own.length) items.push(itemForCrew(member, own));
    }
    for (const alert of alerts.filter(alert => alert.crew_id === null)) items.push(itemForCabin(alert));
    return items.sort((a, b) => rank(b.tone) - rank(a.tone) || a.owner.localeCompare(b.owner));
  }

  function createView() {
    const root = document.createElement("article");
    root.className = "decision-item";
    const head = document.createElement("div");
    head.className = "decision-item-head";
    const owner = document.createElement("strong");
    owner.className = "decision-owner";
    const badge = document.createElement("span");
    badge.className = "badge decision-badge";
    const message = document.createElement("p");
    message.className = "decision-message";
    const meta = document.createElement("p");
    meta.className = "decision-meta";
    const why = document.createElement("ul");
    why.className = "decision-why";
    head.append(owner, badge);
    root.append(head, message, meta, why);
    return { root, owner, badge, message, meta, why, key: null, notesKey: null };
  }

  function updateView(view, item) {
    helpers.setTone(view.root, item.tone);
    helpers.setTone(view.badge, item.tone);
    setText(view.owner, item.owner);
    setText(view.badge, item.badge);
    setText(view.message, item.message);
    // The sentence may be clipped on short screens; keep the full text in the tooltip.
    view.message.title = item.message;
    setText(view.meta, item.meta);
    view.meta.hidden = !item.meta;
    const key = JSON.stringify(item.notes);
    if (key !== view.notesKey) {
      view.notesKey = key;
      while (view.why.children.length > item.notes.length) view.why.lastElementChild.remove();
      item.notes.forEach((text, index) => {
        let chip = view.why.children[index];
        if (!chip) {
          chip = document.createElement("li");
          chip.className = "decision-note";
          view.why.append(chip);
        }
        setText(chip, text);
      });
      view.why.hidden = item.notes.length === 0;
    }
  }

  // The empty state is a single reused view: it must never linger behind real decisions.
  function showEmpty(text, tone) {
    if (!emptyView) {
      emptyView = createView();
      emptyView.badge.hidden = true;
      emptyView.message.hidden = true;
      emptyView.meta.hidden = true;
      emptyView.why.hidden = true;
    }
    emptyView.owner.textContent = text;
    helpers.setTone(emptyView.root, tone);
    if (list.firstChild !== emptyView.root || list.children.length !== 1) {
      list.replaceChildren(emptyView.root);
    }
  }

  function clearEmpty() {
    if (!emptyView) return;
    emptyView.root.remove();
    emptyView = null;
  }

  function update(frame, stale) {
    if (stale) {
      for (const view of views.values()) view.root.remove();
      views.clear();
      note.textContent = "NO DATA · Decision state unknown";
      showEmpty("NO DATA · Waiting for current telemetry.", "unknown");
      return;
    }
    const items = collect(frame);
    const ids = new Set(items.map(item => item.key));
    for (const [key, view] of views) {
      if (!ids.has(key)) { view.root.remove(); views.delete(key); }
    }
    if (!items.length) {
      for (const view of views.values()) view.root.remove();
      views.clear();
      note.textContent = "No decisions require attention";
      showEmpty("All crew nominal", "nominal");
      return;
    }
    clearEmpty();
    note.textContent = `${items.length} need attention`;
    items.forEach((item, index) => {
      let view = views.get(item.key);
      if (!view) { view = createView(); view.key = item.key; views.set(item.key, view); }
      updateView(view, item);
      if (list.children[index] !== view.root) list.insertBefore(view.root, list.children[index] ?? null);
    });
  }

  return { update };
}
