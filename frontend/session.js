export const VITAL_METRICS = [
  "heart_rate", "spo2", "resp_rate", "core_temp", "hrv_rmssd", "systolic_bp", "diastolic_bp",
];
export const CHART_FRAME_LIMIT = 120;
export const finite = value => typeof value === "number" && Number.isFinite(value);
export const usable = reading => reading?.quality === "ok" && finite(reading.value);

export function validFrame(frame) {
  return frame?.contract_version === "0.2.0" && Number.isInteger(frame.tick) && frame.tick >= 0
    && finite(frame.mission_elapsed_s) && Number.isFinite(Date.parse(frame.ts))
    && typeof frame.scenario === "string" && Array.isArray(frame.crew) && frame.crew.length === 4
    && frame.crew.every(member => member?.id && member.vitals)
    && frame.cabin && Array.isArray(frame.alerts);
}

export function createSession() {
  let frames = [];
  let scenario = null;
  let segment = 0;
  let revision = 0;
  const archive = [];
  const keys = new Set();
  const latestAlerts = new Map();

  function ingest(incoming, { live = false } = {}) {
    if (!validFrame(incoming)) return false;
    const last = frames.at(-1);
    const restart = live && last && (incoming.tick < last.tick
      || (incoming.tick === last.tick && incoming.ts !== last.ts));
    if (scenario !== incoming.scenario || restart) {
      frames = [];
      keys.clear();
      latestAlerts.clear();
      scenario = incoming.scenario;
      segment += 1;
    }
    const key = `${incoming.tick}:${incoming.ts}`;
    if (keys.has(key)) return false;
    if (last && !restart && incoming.scenario === last.scenario && incoming.tick < last.tick) return false;
    keys.add(key);
    frames.push(incoming);
    archive.push({ frame: incoming, segment });
    for (const alert of incoming.alerts) {
      if (!alert.crew_id) continue;
      const old = latestAlerts.get(alert.crew_id);
      const ranks = { critical: 3, warning: 2, caution: 1 };
      if (!old || Date.parse(alert.last_seen) > Date.parse(old.last_seen)
        || (alert.last_seen === old.last_seen && (ranks[alert.severity] ?? 0) >= (ranks[old.severity] ?? 0))) {
        latestAlerts.set(alert.crew_id, alert);
      }
    }
    revision += 1;
    return true;
  }

  function hydrate(payload) {
    if (!payload || !Array.isArray(payload.frames) || typeof payload.scenario !== "string") return false;
    const candidates = payload.frames.filter(item => validFrame(item) && item.scenario === payload.scenario);
    for (const incoming of candidates.sort((a, b) => a.tick - b.tick)) ingest(incoming);
    return candidates.length > 0;
  }

  return {
    ingest, hydrate,
    latestAlert: crewId => latestAlerts.get(crewId) ?? null,
    get frames() { return frames; },
    get chartFrames() { return frames.slice(-CHART_FRAME_LIMIT); },
    get archive() { return archive; },
    get latest() { return frames.at(-1) ?? null; },
    get revision() { return revision; },
    get scenario() { return scenario; },
  };
}

export function metricSeries(frames, crewId, metric) {
  return frames.map(frame => {
    const member = frame.crew.find(person => person.id === crewId);
    const reading = member?.vitals[metric];
    return {
      tick: frame.tick, time: Date.parse(frame.ts),
      value: usable(reading) ? reading.value : null,
      baseline: usable(reading) && finite(reading.baseline_delta) ? reading.value - reading.baseline_delta : null,
      status: usable(reading) ? reading.status : "unknown",
      activity: member?.activity ?? "unknown",
    };
  });
}

export function chartEvents(frames, crewId) {
  const events = [];
  const seen = new Set();
  let previousActivity = null;
  for (const frame of frames) {
    const activity = frame.crew.find(member => member.id === crewId)?.activity ?? "unknown";
    if (previousActivity !== null && previousActivity !== activity) {
      events.push({ kind: "activity", time: Date.parse(frame.ts), label: `Activity: ${activity}` });
    }
    previousActivity = activity;
    for (const alert of frame.alerts.filter(item => item.crew_id === crewId)) {
      const key = `${alert.id}:${alert.first_seen}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (Number.isFinite(Date.parse(alert.first_seen))) {
        events.push({ kind: "alert", time: Date.parse(alert.first_seen), label: alert.message, severity: alert.severity });
      }
    }
  }
  return events;
}

function csvCell(value) {
  if (value === null || value === undefined) return "";
  let text = String(value);
  if (typeof value === "string" && /^[=+@\-\t\r\n]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function* sessionCsv(archive) {
  yield "time,mission_elapsed_s,scenario,session_segment,tick,crew_id,crew_name,metric,value,baseline_delta,status,quality,unit,reading_time\r\n";
  for (const { frame, segment } of archive) {
    for (const member of frame.crew) {
      for (const metric of VITAL_METRICS) {
        const reading = member.vitals[metric];
        yield [frame.ts, frame.mission_elapsed_s, frame.scenario, segment, frame.tick, member.id, member.name,
          metric, reading?.value, reading?.baseline_delta, usable(reading) ? reading.status : "unknown",
          reading?.quality ?? "missing", reading?.unit, reading?.ts].map(csvCell).join(",") + "\r\n";
      }
    }
  }
}
