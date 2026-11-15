const CONTRACT_VERSION = "0.2.0";
const MOCK_INTERVAL_MS = 1000;
const MOCK_CYCLE_TICKS = 48;
const MOCK_GAP_AT_TICK = 40;
const MOCK_GAP_DURATION_MS = 12000;
const MOCK_MISSION_START_S = 12 * 24 * 60 * 60;
const MOCK_SECONDS_PER_TICK = 60;
const MOCK_BASELINES = [
  { heart_rate: 64, spo2: 98, resp_rate: 14, core_temp: 36.7, hrv_rmssd: 48, systolic_bp: 118, diastolic_bp: 76 },
  { heart_rate: 68, spo2: 98, resp_rate: 15, core_temp: 36.8, hrv_rmssd: 51, systolic_bp: 120, diastolic_bp: 78 },
  { heart_rate: 66, spo2: 99, resp_rate: 14, core_temp: 36.6, hrv_rmssd: 54, systolic_bp: 116, diastolic_bp: 74 },
  { heart_rate: 67, spo2: 98, resp_rate: 15, core_temp: 36.7, hrv_rmssd: 46, systolic_bp: 121, diastolic_bp: 79 },
];
const MOCK_UNITS = {
  heart_rate: "bpm", spo2: "%", resp_rate: "breaths/min", core_temp: "°C", hrv_rmssd: "ms",
  systolic_bp: "mmHg", diastolic_bp: "mmHg", pp_o2: "mmHg", pp_co2: "mmHg",
  cabin_pressure: "mmHg", cabin_temp: "°C", rel_humidity: "%", radiation_dose_rate: "µGy/h",
};
const MOCK_CREW = [
  { name: "Ayşe", role: "Commander" }, { name: "Can", role: "Flight Engineer" },
  { name: "Ece", role: "Mission Specialist" }, { name: "Mert", role: "Flight Surgeon" },
];
const MOCK_WAVE = [0, 1, 0, -1, 0, 2, 1, 0];

function reading(metric, value, ts, baseline = null, status = "nominal", quality = "ok") {
  return { value, unit: MOCK_UNITS[metric], ts, quality, status,
    baseline_delta: value === null || baseline === null ? null : Math.round((value - baseline) * 100) / 100 };
}
function mockAlert(id, crew_id, metric, severity, message, value, threshold, comparator, note, ts, first_seen, consecutive_count) {
  return { id, crew_id, metric, severity, message, rule_id: `mock-${id}`, consecutive_count,
    first_seen, last_seen: ts, why: [{ metric, value, threshold, comparator, ts, note }] };
}
function makeFrame(tick, clock) {
  const phase = tick % MOCK_CYCLE_TICKS;
  const ts = new Date(clock + tick * MOCK_SECONDS_PER_TICK * 1000).toISOString();
  const at = offset => new Date(clock + (tick - phase + offset) * MOCK_SECONDS_PER_TICK * 1000).toISOString();
  const baselineDemo = phase >= 6;
  const watchDemo = phase >= 12 && phase < 20;
  const actionDemo = phase >= 20 && phase < 30;
  const missingDemo = phase >= 30 && phase < 40;
  const alerts = [];
  const crew = MOCK_CREW.map((person, index) => {
    const baseline = MOCK_BASELINES[index];
    const vitals = Object.fromEntries(Object.entries(baseline).map(([metric, value]) => {
      const wave = metric === "heart_rate" ? MOCK_WAVE[(tick + index) % MOCK_WAVE.length] : 0;
      return [metric, reading(metric, value + wave, ts, value)];
    }));
    const member = { id: `crew-${index + 1}`, ...person, vitals, activity: "resting",
      cumulative_dose_msv: null, event_dose_msv: null, status: "nominal", decision: "normal" };
    if (baselineDemo && (index === 0 || index === 3)) {
      vitals.heart_rate = reading("heart_rate", 80, ts, baseline.heart_rate, index === 0 ? "caution" : "nominal");
      if (index === 0) {
        member.decision = "watch"; member.status = "caution";
        alerts.push(mockAlert("ayse-baseline", member.id, "heart_rate", "caution",
          "MOCK: Watch decision for Ayşe's personal baseline deviation.",
          16, null, ">", "MOCK: Heart rate 80 bpm, personal baseline 64 bpm; difference +16. This is a fixed demo decision. Mert also has 80 bpm, with a +13 difference and a Normal decision.",
          ts, at(6), phase - 5));
      }
    }
    if ((watchDemo || actionDemo) && index === 2) {
      member.activity = "exercise";
      member.decision = actionDemo ? "action" : "watch";
      member.status = actionDemo ? "critical" : "caution";
      vitals.spo2 = reading("spo2", actionDemo ? 93 : 96, ts, baseline.spo2, member.status);
      alerts.push(mockAlert("ece-spo2", member.id, "spo2", member.status,
        actionDemo ? "MOCK: Action decision for Ece; SpO₂ is 6 points below baseline."
          : "MOCK: Watch decision for Ece; SpO₂ is 3 points below baseline.",
        actionDemo ? -6 : -3, null, "<",
        "MOCK: Ece is exercising. Only the SpO₂ baseline deviation is shown; exercise heart rate and respiration are not evidence. No threshold or decision was calculated.",
        ts, at(12), phase - 11));
    }
    if (missingDemo && index === 1) {
      member.decision = "unknown"; member.status = "unknown";
      vitals.heart_rate = reading("heart_rate", null, ts, null, "unknown", "missing");
      vitals.hrv_rmssd = reading("hrv_rmssd", 51, at(29), 51, "unknown", "stale");
      vitals.spo2 = reading("spo2", 98, ts, 98, "unknown", "sensor_fault");
      alerts.push(mockAlert("can-missing", member.id, "heart_rate", "caution",
        "MOCK: Insufficient data to make a decision for Can.", null, null, "missing",
        "MOCK: No heart rate sensor data. Missing readings are not treated as normal; HRV is stale and SpO₂ has a sensor fault in this example.",
        ts, at(30), phase - 29));
    }
    return member;
  });
  const o2Active = watchDemo || actionDemo;
  const status = actionDemo ? "critical" : watchDemo ? "caution" : "nominal";
  const cabin = {
    pp_o2: reading("pp_o2", o2Active ? 156 - (phase - 12) : 159, ts, null, status),
    pp_co2: reading("pp_co2", 1.8, ts), cabin_pressure: reading("cabin_pressure", 760, ts),
    cabin_temp: reading("cabin_temp", 22, ts), rel_humidity: reading("rel_humidity", 44, ts),
    radiation_dose_rate: reading("radiation_dose_rate", 1.1, ts), status,
    hours_until_o2_limit: watchDemo ? (20 - phase) / 4 : actionDemo ? 0 : null,
    hours_until_co2_limit: null,
  };
  if (o2Active) {
    alerts.push(mockAlert("cabin-o2", null, "pp_o2", status,
      "MOCK: Cabin O₂ is falling; the CO₂ countdown is unaffected.", cabin.pp_o2.value, null, "<",
      "MOCK: In this fixed demo sequence, the O₂ countdown decreases to zero. The CO₂ countdown remains null. No threshold or atmosphere model was calculated.",
      ts, at(12), phase - 11));
  }
  return { contract_version: CONTRACT_VERSION, tick, ts,
    mission_elapsed_s: MOCK_MISSION_START_S + tick * MOCK_SECONDS_PER_TICK,
    scenario: missingDemo ? "sensor_dropout" : o2Active ? "hypoxia" : "nominal",
    simulated: true, crew, cabin, alerts, comms_one_way_delay_s: 720, messages: [] };
}
export function startMock(onFrame) {
  const clock = Date.now();
  let tick = 0;
  let stopped = false;
  let timer = null;
  function emit() {
    if (stopped) return;
    onFrame(makeFrame(tick, clock));
    tick += 1;
    const delay = tick % MOCK_CYCLE_TICKS === MOCK_GAP_AT_TICK ? MOCK_GAP_DURATION_MS : MOCK_INTERVAL_MS;
    timer = window.setTimeout(emit, delay);
  }
  emit();
  return () => { stopped = true; window.clearTimeout(timer); };
}
