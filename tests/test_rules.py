"""Unit and integration tests for the rules engine (backend/rules.py)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend import rules as r
from backend.atmosphere import load_profile
from backend.models import (
    Activity,
    CabinAtmosphere,
    Comparator,
    CrewDecision,
    CrewMember,
    CrewVitals,
    Metric,
    Quality,
    Reading,
    Scenario,
    Severity,
    Status,
    TelemetryFrame,
)

BASE_TIME = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def make_reading(
    val: float | None,
    unit: str = "mmHg",
    quality: Quality = Quality.ok,
    ts: datetime = BASE_TIME,
    baseline_delta: float | None = None,
) -> Reading:
    status = Status.unknown if (val is None or quality != Quality.ok) else Status.nominal
    return Reading(
        value=val,
        unit=unit,
        ts=ts,
        quality=quality,
        status=status,
        baseline_delta=baseline_delta,
    )


def make_vitals(
    hr: float = 64.0,
    spo2: float = 98.0,
    temp: float = 36.6,
    rr: float = 14.0,
    hrv: float = 45.0,
    sys_bp: float = 120.0,
    dia_bp: float = 80.0,
    ts: datetime = BASE_TIME,
) -> CrewVitals:
    return CrewVitals(
        heart_rate=make_reading(hr, "bpm", ts=ts),
        hrv_rmssd=make_reading(hrv, "ms", ts=ts),
        spo2=make_reading(spo2, "%", ts=ts),
        resp_rate=make_reading(rr, "breaths/min", ts=ts),
        core_temp=make_reading(temp, "°C", ts=ts),
        systolic_bp=make_reading(sys_bp, "mmHg", ts=ts),
        diastolic_bp=make_reading(dia_bp, "mmHg", ts=ts),
    )


def make_cabin(
    pp_co2: float = 1.0,
    pp_o2: float = 160.0,
    pressure: float = 760.0,
    temp: float = 22.0,
    humidity: float = 45.0,
    rad: float = 3.125,
    ts: datetime = BASE_TIME,
    co2_unit: str = "mmHg",
    o2_unit: str = "mmHg",
    p_unit: str = "mmHg",
) -> CabinAtmosphere:
    return CabinAtmosphere(
        pp_co2=make_reading(pp_co2, co2_unit, ts=ts),
        pp_o2=make_reading(pp_o2, o2_unit, ts=ts),
        cabin_pressure=make_reading(pressure, p_unit, ts=ts),
        cabin_temp=make_reading(temp, "°C", ts=ts),
        rel_humidity=make_reading(humidity, "%", ts=ts),
        radiation_dose_rate=make_reading(rad, "µGy/h", ts=ts),
        status=Status.nominal,
    )


def make_frame(
    tick: int = 0,
    elapsed_s: float = 0.0,
    ts: datetime = BASE_TIME,
    cabin: CabinAtmosphere | None = None,
    crew_list: list[CrewMember] | None = None,
) -> TelemetryFrame:
    if cabin is None:
        cabin = make_cabin(ts=ts)
    if crew_list is None:
        crew_list = [
            CrewMember(
                id="crew-1",
                name="Alper",
                role="CDR",
                vitals=make_vitals(64, 98, 36.6, 14, ts=ts),
                activity=Activity.resting,
            ),
            CrewMember(
                id="crew-2",
                name="Deniz",
                role="PLT",
                vitals=make_vitals(70, 97, 36.8, 15, ts=ts),
                activity=Activity.resting,
            ),
            CrewMember(
                id="crew-3",
                name="Ece",
                role="MS1",
                vitals=make_vitals(58, 98, 36.5, 13, ts=ts),
                activity=Activity.resting,
            ),
            CrewMember(
                id="crew-4",
                name="Baran",
                role="MS2",
                vitals=make_vitals(67, 97, 36.7, 14, ts=ts),
                activity=Activity.resting,
            ),
        ]
    return TelemetryFrame(
        tick=tick,
        ts=ts,
        mission_elapsed_s=elapsed_s,
        scenario=Scenario.nominal,
        simulated=True,
        crew=crew_list,
        cabin=cabin,
        alerts=[],
        comms_one_way_delay_s=0.0,
        messages=[],
    )


def test_baseline_delta_calculation() -> None:
    engine = r.RulesEngine()
    frame = make_frame()

    vitals = make_vitals(hr=83.0)
    bad_reading = vitals.heart_rate.model_copy(update={"baseline_delta": 999.0})
    updated_vitals = vitals.model_copy(update={"heart_rate": bad_reading})
    updated_crew = [
        c.model_copy(update={"vitals": updated_vitals}) if c.id == "crew-3" else c for c in frame.crew
    ]
    frame = frame.model_copy(update={"crew": updated_crew})

    evaluated = engine.evaluate(frame)
    crew3 = next(c for c in evaluated.crew if c.id == "crew-3")

    # 83 - 58 = 25.0
    assert crew3.vitals.heart_rate.baseline_delta == 25.0
    assert crew3.vitals.heart_rate.baseline_delta != 999.0


def test_missing_baseline_raises_error() -> None:
    engine = r.RulesEngine()
    frame = make_frame()
    c_unknown = CrewMember(
        id="crew-9",
        name="Bilinmeyen",
        role="X",
        vitals=make_vitals(),
        activity=Activity.resting,
    )
    frame_bad = frame.model_copy(update={"crew": [c_unknown] + list(frame.crew[1:])})
    with pytest.raises(ValueError, match="no baseline for crew-9"):
        engine.evaluate(frame_bad)


def test_consecutive_measurements_lifecycle() -> None:
    engine = r.RulesEngine(co2_window_s=None)

    f1 = make_frame(tick=1, cabin=make_cabin(pp_co2=2.7))
    e1 = engine.evaluate(f1)
    assert not any(a.rule_id == "ppco2-caution" for a in e1.alerts)

    f2 = make_frame(tick=2, cabin=make_cabin(pp_co2=2.7))
    e2 = engine.evaluate(f2)
    assert not any(a.rule_id == "ppco2-caution" for a in e2.alerts)

    f3 = make_frame(tick=3, cabin=make_cabin(pp_co2=2.7))
    e3 = engine.evaluate(f3)
    alert = next((a for a in e3.alerts if a.rule_id == "ppco2-caution"), None)
    assert alert is not None
    assert alert.consecutive_count == 3
    assert alert.severity == Severity.caution

    f4 = make_frame(tick=4, cabin=make_cabin(pp_co2=1.0))
    e4 = engine.evaluate(f4)
    assert not any(a.rule_id == "ppco2-caution" for a in e4.alerts)


def test_exercise_gate_suppresses_cardiac_load() -> None:
    engine = r.RulesEngine()

    for tick in range(1, 4):
        vitals = make_vitals(hr=104.0)  # +40
        c1 = CrewMember(
            id="crew-1",
            name="Alper",
            role="CDR",
            vitals=vitals,
            activity=Activity.exercise,
        )
        frame = make_frame(
            tick=tick,
            crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        e = engine.evaluate(frame)

    assert not any(a.id == "crew-1:karar-kalp_yuku" for a in e.alerts)
    c1_eval = next(c for c in e.crew if c.id == "crew-1")
    assert c1_eval.vitals.heart_rate.status == Status.nominal

    engine.reset()
    for tick in range(1, 4):
        vitals = make_vitals(hr=104.0)
        c1 = CrewMember(
            id="crew-1",
            name="Alper",
            role="CDR",
            vitals=vitals,
            activity=Activity.resting,
        )
        frame = make_frame(
            tick=tick,
            crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        e_resting = engine.evaluate(frame)

    ky_alert = next((a for a in e_resting.alerts if a.id == "crew-1:karar-kalp_yuku"), None)
    assert ky_alert is not None
    assert ky_alert.severity == Severity.critical


def test_unknown_and_sleep_activity_gate() -> None:
    engine = r.RulesEngine()

    for act in (Activity.unknown, Activity.sleep):
        engine.reset()
        for tick in range(1, 4):
            vitals = make_vitals(hr=99.0, spo2=94.0)
            c1 = CrewMember(
                id="crew-1",
                name="Alper",
                role="CDR",
                vitals=vitals,
                activity=act,
            )
            cabin = make_cabin(pp_o2=149.0, pressure=760.0)
            frame = make_frame(
                tick=tick,
                cabin=cabin,
                crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
            )
            evaluated = engine.evaluate(frame)

        assert not any(a.id == "crew-1:karar-kalp_yuku" for a in evaluated.alerts)
        hipoksi = next((a for a in evaluated.alerts if a.id == "crew-1:karar-olasi_hipoksi"), None)
        assert hipoksi is not None
        assert not any(ev.metric == Metric.heart_rate for ev in hipoksi.why)


def test_missing_data_status_and_alerts() -> None:
    engine = r.RulesEngine()

    missing_spo2 = make_reading(None, "%", quality=Quality.missing)
    vitals = make_vitals().model_copy(update={"spo2": missing_spo2})
    c1 = CrewMember(id="crew-1", name="Alper", role="CDR", vitals=vitals, activity=Activity.resting)
    f1 = make_frame(tick=1, crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"])
    e1 = engine.evaluate(f1)

    c1_e1 = next(c for c in e1.crew if c.id == "crew-1")
    assert c1_e1.vitals.spo2.status == Status.unknown
    assert c1_e1.vitals.spo2.baseline_delta is None
    assert c1_e1.status == Status.unknown
    assert c1_e1.decision == CrewDecision.unknown

    f2 = make_frame(tick=2, crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"])
    e2 = engine.evaluate(f2)
    assert not any(a.id == "crew-1:missing-spo2" for a in e2.alerts)

    f3 = make_frame(tick=3, crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"])
    e3 = engine.evaluate(f3)
    miss_alert = next((a for a in e3.alerts if a.id == "crew-1:missing-spo2"), None)
    assert miss_alert is not None
    assert miss_alert.severity == Severity.caution
    assert miss_alert.rule_id == "missing-spo2"
    assert miss_alert.why[0].comparator == Comparator.missing
    assert miss_alert.why[0].threshold is None
    assert "no data for 3 readings" in miss_alert.why[0].note

    c1_e3 = next(c for c in e3.crew if c.id == "crew-1")
    assert c1_e3.decision == CrewDecision.watch


def test_crew_co2_severity_bound_to_cabin_limit() -> None:
    engine_instant = r.RulesEngine(co2_window_s=None)

    vitals_co2 = make_vitals().model_copy(update={"resp_rate": make_reading(18.0, "bpm")})
    c1_co2 = CrewMember(id="crew-1", name="Alper", role="CDR", vitals=vitals_co2, activity=Activity.resting)

    for tick in (1, 2, 3):
        f = make_frame(
            tick=tick,
            cabin=make_cabin(pp_co2=2.7),
            crew_list=[c1_co2] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        res = engine_instant.evaluate(f)

    crew_alert = next((a for a in res.alerts if a.id == "crew-1:karar-olasi_co2_etkisi"), None)
    assert crew_alert is not None
    assert crew_alert.severity == Severity.caution
    assert not any(a.id == "cabin:ppco2-limit" for a in res.alerts)

    for tick in (4, 5):
        f = make_frame(
            tick=tick,
            cabin=make_cabin(pp_co2=3.4),
            crew_list=[c1_co2] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        res = engine_instant.evaluate(f)
        assert not any(a.id == "cabin:ppco2-limit" for a in res.alerts)
        ca = next(a for a in res.alerts if a.id == "crew-1:karar-olasi_co2_etkisi")
        assert ca.severity == Severity.caution

    f6 = make_frame(
        tick=6,
        cabin=make_cabin(pp_co2=3.4),
        crew_list=[c1_co2] + [c for c in make_frame().crew if c.id != "crew-1"],
    )
    res6 = engine_instant.evaluate(f6)
    assert any(a.id == "cabin:ppco2-limit" for a in res6.alerts)
    ca6 = next(a for a in res6.alerts if a.id == "crew-1:karar-olasi_co2_etkisi")
    assert ca6.severity == Severity.critical

    engine_win = r.RulesEngine(co2_window_s=3600.0)
    t0 = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    f_nom = make_frame(
        tick=1,
        cabin=make_cabin(pp_co2=2.0, ts=t0),
        crew_list=[c1_co2] + [c for c in make_frame().crew if c.id != "crew-1"],
    )
    engine_win.evaluate(f_nom)

    for i in range(1, 4):
        ti = t0 + timedelta(seconds=i * 10)
        f_co2 = make_frame(
            tick=1 + i,
            cabin=make_cabin(pp_co2=3.4, ts=ti),
            crew_list=[c1_co2] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        res_win = engine_win.evaluate(f_co2)

    assert not any(a.id == "cabin:ppco2-limit" for a in res_win.alerts)
    ca_win = next(a for a in res_win.alerts if a.id == "crew-1:karar-olasi_co2_etkisi")
    assert ca_win.severity == Severity.caution

    t_later = t0 + timedelta(seconds=3700)
    for i in range(3):
        f_later = make_frame(
            tick=10 + i,
            cabin=make_cabin(pp_co2=3.4, ts=t_later + timedelta(seconds=i * 10)),
            crew_list=[c1_co2] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        res_later = engine_win.evaluate(f_later)

    assert any(a.id == "cabin:ppco2-limit" for a in res_later.alerts)
    ca_crit = next(a for a in res_later.alerts if a.id == "crew-1:karar-olasi_co2_etkisi")
    assert ca_crit.severity == Severity.critical


def test_pio2_derivation_and_reversibility() -> None:
    reference_path = Path(__file__).resolve().parents[1] / "data" / "reference" / "ranges.json"
    profile = load_profile(reference_path, "mars_transit")
    water_vapour = profile.ranges.water_vapour_mmhg  # 47.0

    r_o2 = make_reading(160.0, "mmHg")
    r_pb = make_reading(760.0, "mmHg")
    pio2 = r.derive_pio2(r_o2, r_pb, water_vapour)
    assert pio2 is not None
    assert pio2 == pytest.approx(150.105, rel=1e-3)

    recovered_pp_o2 = profile.pp_o2_from_pio2(pio2)
    assert recovered_pp_o2 == pytest.approx(160.0, rel=1e-5)

    r_o2_kpa = make_reading(21.33157, "kPa")
    r_pb_kpa = make_reading(101.32495, "kPa")
    pio2_kpa = r.derive_pio2(r_o2_kpa, r_pb_kpa, water_vapour)
    assert pio2_kpa is not None
    assert pio2_kpa == pytest.approx(150.105, rel=1e-3)

    r_o2_none = make_reading(None, "mmHg", quality=Quality.missing)
    assert r.derive_pio2(r_o2_none, r_pb, water_vapour) is None


def test_hypoxia_severity_tiers() -> None:
    engine = r.RulesEngine()

    for tick in range(1, 4):
        vitals = make_vitals(spo2=94.0)
        c1 = CrewMember(
            id="crew-1",
            name="Alper",
            role="CDR",
            vitals=vitals,
            activity=Activity.exercise,
        )
        cabin = make_cabin(pp_o2=143.9, pressure=760.0)
        frame = make_frame(
            tick=tick,
            cabin=cabin,
            crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        e_caution = engine.evaluate(frame)

    c1_c = next(c for c in e_caution.crew if c.id == "crew-1")
    assert c1_c.decision == CrewDecision.watch
    alert_c = next(a for a in e_caution.alerts if a.id == "crew-1:karar-olasi_hipoksi")
    assert alert_c.severity == Severity.caution

    engine.reset()
    for tick in range(1, 4):
        vitals = make_vitals(spo2=91.0)
        c1 = CrewMember(
            id="crew-1",
            name="Alper",
            role="CDR",
            vitals=vitals,
            activity=Activity.exercise,
        )
        cabin = make_cabin(pp_o2=143.9, pressure=760.0)
        frame = make_frame(
            tick=tick,
            cabin=cabin,
            crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        e_critical = engine.evaluate(frame)

    c1_crit = next(c for c in e_critical.crew if c.id == "crew-1")
    assert c1_crit.decision == CrewDecision.action
    alert_crit = next(a for a in e_critical.alerts if a.id == "crew-1:karar-olasi_hipoksi")
    assert alert_crit.severity == Severity.critical


def test_pio2_seamless_caution_to_critical_transition() -> None:
    engine = r.RulesEngine()

    def pp_o2_for_pio2(pio2: float) -> float:
        return pio2 * 760.0 / 713.0

    seq1 = [150.0, 140.0, 130.0, 126.0, 126.0, 126.0]
    alerts_seq1: list[list[str]] = []
    for tick, pio2_val in enumerate(seq1, 1):
        frame = make_frame(
            tick=tick,
            cabin=make_cabin(pp_o2=pp_o2_for_pio2(pio2_val), pressure=760.0),
        )
        evaluated = engine.evaluate(frame)
        o2_alerts = [a.rule_id for a in evaluated.alerts if "pio2" in a.rule_id]
        alerts_seq1.append(o2_alerts)

    assert alerts_seq1[0] == []
    assert alerts_seq1[1] == []
    assert alerts_seq1[2] == []
    assert alerts_seq1[3] == ["pio2-normoxia-low"]
    assert alerts_seq1[4] == ["pio2-normoxia-low"]
    assert alerts_seq1[5] == ["pio2-hypoxia-low"]

    engine.reset()
    seq2 = [140.0, 140.0, 140.0, 126.0, 126.0, 126.0]
    alerts_seq2: list[list[str]] = []
    for tick, pio2_val in enumerate(seq2, 1):
        frame = make_frame(
            tick=tick,
            cabin=make_cabin(pp_o2=pp_o2_for_pio2(pio2_val), pressure=760.0),
        )
        evaluated = engine.evaluate(frame)
        o2_alerts = [a.rule_id for a in evaluated.alerts if "pio2" in a.rule_id]
        alerts_seq2.append(o2_alerts)

    assert alerts_seq2[0] == []
    assert alerts_seq2[1] == []
    assert alerts_seq2[2] == ["pio2-normoxia-low"]
    assert alerts_seq2[3] == ["pio2-normoxia-low"]
    assert alerts_seq2[4] == ["pio2-normoxia-low"]
    assert alerts_seq2[5] == ["pio2-hypoxia-low"]


def test_co2_temp_and_radiation_decisions() -> None:
    engine = r.RulesEngine()

    for tick in range(1, 4):
        vitals = make_vitals(rr=18.0)
        c1 = CrewMember(
            id="crew-1",
            name="Alper",
            role="CDR",
            vitals=vitals,
            activity=Activity.exercise,
        )
        cabin = make_cabin(pp_co2=2.7)  # > 2.5 (caution)
        frame = make_frame(
            tick=tick,
            cabin=cabin,
            crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        e_co2 = engine.evaluate(frame)

    co2_alert = next((a for a in e_co2.alerts if a.id == "crew-1:karar-olasi_co2_etkisi"), None)
    assert co2_alert is not None
    assert co2_alert.severity == Severity.caution

    engine.reset()
    for tick in range(1, 4):
        vitals = make_vitals(temp=37.8)
        c1 = CrewMember(
            id="crew-1",
            name="Alper",
            role="CDR",
            vitals=vitals,
            activity=Activity.resting,
        )
        frame = make_frame(
            tick=tick,
            crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        e_temp = engine.evaluate(frame)

    temp_alert = next((a for a in e_temp.alerts if a.id == "crew-1:karar-isi_sapmasi"), None)
    assert temp_alert is not None
    assert temp_alert.severity == Severity.critical

    engine.reset()
    sep_ctx = r.SepContext(
        sep_active=True,
        event_dose_rate_msv_per_h=25.0,
        remaining_event_h=5.0,
    )
    for tick in range(1, 4):
        c1 = CrewMember(
            id="crew-1",
            name="Alper",
            role="CDR",
            vitals=make_vitals(),
            activity=Activity.resting,
            event_dose_msv=100.0,
            cumulative_dose_msv=200.0,
        )
        frame = make_frame(
            tick=tick,
            crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
        )
        e_rad = engine.evaluate(frame, sep=sep_ctx)

    rad_alert = next((a for a in e_rad.alerts if a.id == "crew-1:karar-radyasyon_etkisi"), None)
    assert rad_alert is not None
    assert rad_alert.severity == Severity.caution

    engine.reset()
    c_clean = CrewMember(
        id="crew-1",
        name="Alper",
        role="CDR",
        vitals=make_vitals(),
        activity=Activity.resting,
        event_dose_msv=0.0,
        cumulative_dose_msv=200.0,
    )
    frame_clean = make_frame(
        tick=1,
        crew_list=[c_clean] + [c for c in make_frame().crew if c.id != "crew-1"],
    )
    e_clean = engine.evaluate(frame_clean, sep=None)
    assert not any(a.id == "crew-1:karar-radyasyon_etkisi" for a in e_clean.alerts)
    c1_clean_res = next(c for c in e_clean.crew if c.id == "crew-1")
    assert c1_clean_res.status == Status.nominal


def test_co2_moving_average_window() -> None:
    engine = r.RulesEngine(co2_window_s=3600.0)

    for minute in range(5):
        frame = make_frame(
            tick=minute,
            elapsed_s=minute * 60.0,
            cabin=make_cabin(pp_co2=2.0),
        )
        engine.evaluate(frame)

    for tick in range(5, 8):
        frame = make_frame(
            tick=tick,
            elapsed_s=tick * 60.0,
            cabin=make_cabin(pp_co2=3.2),
        )
        e = engine.evaluate(frame)

    assert not any(a.rule_id == "ppco2-limit" for a in e.alerts)

    engine_instant = r.RulesEngine(co2_window_s=None)
    for tick in range(1, 4):
        frame = make_frame(
            tick=tick,
            elapsed_s=tick * 60.0,
            cabin=make_cabin(pp_co2=3.2),
        )
        e_inst = engine_instant.evaluate(frame)
    assert any(a.rule_id == "ppco2-limit" for a in e_inst.alerts)
    assert len(engine_instant._co2_window) == 0


def test_alert_lifecycle_and_immutability() -> None:
    engine = r.RulesEngine()

    t0 = BASE_TIME
    for tick in range(1, 4):
        t = t0 + timedelta(seconds=tick * 10)
        vitals = make_vitals(spo2=94.0, ts=t)
        c1 = CrewMember(
            id="crew-1",
            name="Alper",
            role="CDR",
            vitals=vitals,
            activity=Activity.exercise,
        )
        cabin = make_cabin(pp_o2=143.9, ts=t)
        frame_input = make_frame(
            tick=tick,
            ts=t,
            cabin=cabin,
            crew_list=[c1] + [c for c in make_frame(ts=t).crew if c.id != "crew-1"],
        )
        original_frame_json = frame_input.model_dump_json()
        frame_evaluated = engine.evaluate(frame_input)

        assert frame_input.model_dump_json() == original_frame_json

    alert1 = next(a for a in frame_evaluated.alerts if a.id == "crew-1:karar-olasi_hipoksi")
    assert alert1.severity == Severity.caution
    assert len(alert1.why) >= 1
    assert alert1.first_seen <= alert1.last_seen
    first_seen_initial = alert1.first_seen

    t4 = t0 + timedelta(seconds=40)
    vitals_crit = make_vitals(spo2=91.0, ts=t4)
    c1_crit = CrewMember(
        id="crew-1",
        name="Alper",
        role="CDR",
        vitals=vitals_crit,
        activity=Activity.exercise,
    )
    cabin_crit = make_cabin(pp_o2=143.9, ts=t4)
    f4 = make_frame(
        tick=4,
        ts=t4,
        cabin=cabin_crit,
        crew_list=[c1_crit] + [c for c in make_frame(ts=t4).crew if c.id != "crew-1"],
    )
    f4_eval = engine.evaluate(f4)

    alert2 = next(a for a in f4_eval.alerts if a.id == "crew-1:karar-olasi_hipoksi")
    assert alert2.severity == Severity.critical
    assert alert2.first_seen == first_seen_initial
    assert alert2.last_seen == t4


def _evaluate_crew1(
    engine: r.RulesEngine,
    tick: int,
    activity: Activity,
    *,
    hr: float = 64.0,
    spo2: float = 98.0,
    pp_o2: float,
) -> TelemetryFrame:
    c1 = CrewMember(
        id="crew-1",
        name="Alper",
        role="CDR",
        vitals=make_vitals(hr=hr, spo2=spo2),
        activity=activity,
    )
    frame = make_frame(
        tick=tick,
        cabin=make_cabin(pp_o2=pp_o2, pressure=760.0),
        crew_list=[c1] + [c for c in make_frame().crew if c.id != "crew-1"],
    )
    return engine.evaluate(frame)


def test_hypoxia_severity_ignores_pio2_below_hafif_hipoksi() -> None:
    engine = r.RulesEngine()
    low_pp_o2 = 120.0 * 760.0 / 713.0  # PIO2 120 < pio2_hafif_hipoksi_alt
    for tick in range(1, 4):
        evaluated = _evaluate_crew1(engine, tick, Activity.exercise, spo2=95.0, pp_o2=low_pp_o2)
    alert = next(a for a in evaluated.alerts if a.id == "crew-1:karar-olasi_hipoksi")
    assert alert.severity == Severity.caution

    for tick in range(4, 7):
        evaluated = _evaluate_crew1(engine, tick, Activity.exercise, spo2=91.0, pp_o2=low_pp_o2)
    alert = next(a for a in evaluated.alerts if a.id == "crew-1:karar-olasi_hipoksi")
    assert alert.severity == Severity.critical


def test_cardiac_load_alert_holds_through_single_noisy_reading() -> None:
    engine = r.RulesEngine()
    pp_o2 = 145.0 * 760.0 / 713.0
    tick = 0
    for _ in range(3):
        tick += 1
        evaluated = _evaluate_crew1(engine, tick, Activity.resting, hr=80.0, pp_o2=pp_o2)
    assert any(a.id == "crew-1:karar-kalp_yuku" for a in evaluated.alerts)

    for _ in range(engine.consecutive_required - 1):
        tick += 1
        evaluated = _evaluate_crew1(engine, tick, Activity.resting, hr=70.0, pp_o2=pp_o2)
        assert any(a.id == "crew-1:karar-kalp_yuku" for a in evaluated.alerts)

    tick += 1
    evaluated = _evaluate_crew1(engine, tick, Activity.resting, hr=80.0, pp_o2=pp_o2)
    assert any(a.id == "crew-1:karar-kalp_yuku" for a in evaluated.alerts)
    for _ in range(engine.consecutive_required):
        tick += 1
        evaluated = _evaluate_crew1(engine, tick, Activity.resting, hr=70.0, pp_o2=pp_o2)
    assert not any(a.id == "crew-1:karar-kalp_yuku" for a in evaluated.alerts)


def test_cardiac_load_alert_clears_immediately_on_exercise() -> None:
    engine = r.RulesEngine()
    pp_o2 = 145.0 * 760.0 / 713.0
    for tick in range(1, 4):
        evaluated = _evaluate_crew1(engine, tick, Activity.resting, hr=80.0, pp_o2=pp_o2)
    assert any(a.id == "crew-1:karar-kalp_yuku" for a in evaluated.alerts)
    evaluated = _evaluate_crew1(engine, 4, Activity.exercise, hr=80.0, pp_o2=pp_o2)
    assert not any(a.id == "crew-1:karar-kalp_yuku" for a in evaluated.alerts)


def test_zero_magic_numbers_custom_ranges(tmp_path: Path) -> None:
    orig_path = r.DEFAULT_RANGES
    with open(orig_path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    data["kabin"]["ppco2_sari_esik"]["deger"] = 4.0
    for row in data["kurallar"]:
        if row["id"] == "ppco2-caution":
            row["threshold"] = 4.0

    custom_path = tmp_path / "custom_ranges.json"
    custom_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    engine_custom = r.RulesEngine(ranges_path=custom_path, co2_window_s=None)
    assert engine_custom.ppco2_sari_esik == 4.0

    for tick in range(1, 4):
        frame = make_frame(tick=tick, cabin=make_cabin(pp_co2=3.5))
        e = engine_custom.evaluate(frame)

    assert not any(a.rule_id == "ppco2-caution" for a in e.alerts)


def test_reproducibility_and_reset() -> None:
    engine = r.RulesEngine(co2_window_s=None)

    frames = [
        make_frame(tick=1, cabin=make_cabin(pp_co2=2.8)),
        make_frame(tick=2, cabin=make_cabin(pp_co2=2.8)),
        make_frame(tick=3, cabin=make_cabin(pp_co2=2.8)),
        make_frame(tick=4, cabin=make_cabin(pp_co2=1.0)),
    ]

    outputs_1 = [engine.evaluate(f).model_dump(mode="json") for f in frames]

    engine.reset()
    outputs_2 = [engine.evaluate(f).model_dump(mode="json") for f in frames]

    assert outputs_1 == outputs_2
