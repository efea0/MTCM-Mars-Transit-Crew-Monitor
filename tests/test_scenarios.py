"""Consistency tests for the scenario files (data/scenarios/*.json) - owner: Barış (qa).

They also run before simulator.py and rules.py exist. What they check:
1. Format: file name = Scenario enum value; crew/metric/severity match the contract.
2. Thresholds, baselines and model parameters equal data/reference/ranges.json (if the file exists).
3. The condition moments in the expected decision table are recomputed with a mass balance and vital
   model that are independent of the scenario's own events. If someone changes a number without
   updating the table, the test breaks.
4. The scenario does not contradict itself: target values that would trigger a forbidden alert, and
   evidence-rule consistency.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend import models as m

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "data" / "scenarios"
RANGES_PATH = ROOT / "data" / "reference" / "ranges.json"
SCENARIO_FILES = sorted(SCENARIO_DIR.glob("*.json"))
REQUIRED_SCENARIOS = {"nominal", "hypoxia", "hypercapnia", "solar_storm", "sensor_dropout"}

CREW_METRICS = set(m.CrewVitals.model_fields)
CABIN_METRICS = {
    name for name, field in m.CabinAtmosphere.model_fields.items() if field.annotation is m.Reading
}
SEVERITIES = {s.value for s in m.Severity}
CREW_ALERT_EXTRA_METRICS = {"event_dose"}
STATUSES = {s.value for s in m.Status}

GAS_CONSTANT = 8.314
PA_PER_MMHG = 133.322
WATER_VAPOR_MMHG = 47.0
O2_MOLAR_G = 32.0
CO2_MOLAR_G = 44.0
STEP_MIN = 0.25
MINUTES_PER_DAY = 1440.0
TOLERANCE_MIN = 2.0

Scenario = dict[str, Any]
REST_DELTA_KEYS = {
    "heart_rate": "hr_rise_caution",
    "resp_rate": "rr_rise_caution",
    "spo2": "spo2_drop_caution",
}


def load(name: str) -> Scenario:
    return json.loads((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8"))


def all_scenarios() -> list[Scenario]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in SCENARIO_FILES]


def by_rule(s: Scenario, rule_id: str) -> dict[str, Any]:
    return next(a for a in s["expected_alerts"] if a["rule_id"] == rule_id)


def baseline(s: Scenario, crew_id: str, metric: str) -> float:
    return next(c for c in s["crew"] if c["id"] == crew_id)["baseline"][metric]["mean"]


def vital(s: Scenario, crew_id: str, metric: str, t: float) -> float:
    value = baseline(s, crew_id, metric)
    for e in sorted(s["events"], key=lambda e: e["t_min"]):
        if e.get("crew_id") != crew_id or metric not in e.get("targets", {}) or e["t_min"] > t:
            continue
        frac = min(1.0, (t - e["t_min"]) / e["ramp_min"])
        value += (e["targets"][metric] - value) * frac
    return value


def first_time(s: Scenario, predicate: Callable[[float], bool]) -> float | None:
    steps = int(s["duration_min"] / STEP_MIN)
    return next((i * STEP_MIN for i in range(steps + 1) if predicate(i * STEP_MIN)), None)


def metabolic_kg_day(s: Scenario, t: float) -> tuple[float, float]:
    md = s["model"]
    o2 = co2 = 0.0
    for c in s["crew"]:
        r = vital(s, c["id"], "heart_rate", t) / c["baseline"]["heart_rate"]["mean"]
        a = min(1.0, max(0.0, (r - md["r_min"]) / (md["r_max"] - md["r_min"])))
        o2 += md["o2_min"] + (md["o2_max"] - md["o2_min"]) * a
        co2 += md["co2_min"] + (md["co2_max"] - md["co2_min"]) * a
    return o2, co2


def cabin_series(s: Scenario) -> list[tuple[float, float, float]]:
    md = s["model"]
    total = md["total_pressure_mmHg"]

    def mmhg_per_kg(molar_g: float) -> float:
        return 1000 / molar_g * GAS_CONSTANT * md["temperature_K"] / md["volume_m3"] / PA_PER_MMHG

    pp_o2 = s["cabin_initial_pio2_mmHg"] * total / (total - WATER_VAPOR_MMHG)
    pp_co2 = s["cabin_initial"]["pp_co2"]["value"]
    o2_fail = next((e["t_min"] for e in s["events"] if e["type"] == "o2_generation_rate"), None)
    co2_fail = next((e for e in s["events"] if e["type"] == "co2_removal_rate"), None)
    out = []
    for i in range(int(s["duration_min"] / STEP_MIN) + 1):
        t = i * STEP_MIN
        out.append((t, pp_o2 * (total - WATER_VAPOR_MMHG) / total, pp_co2))
        o2_use, co2_make = metabolic_kg_day(s, t)
        o2_net = -o2_use if o2_fail is not None and t >= o2_fail else 0.0
        co2_net = (
            co2_make - co2_fail["value"] * md["co2_removal_kg_day"]
            if co2_fail and t >= co2_fail["t_min"]
            else 0.0
        )
        pp_o2 = max(0.0, pp_o2 + o2_net * mmhg_per_kg(O2_MOLAR_G) * STEP_MIN / MINUTES_PER_DAY)
        pp_co2 = max(0.0, pp_co2 + co2_net * mmhg_per_kg(CO2_MOLAR_G) * STEP_MIN / MINUTES_PER_DAY)
    return out


def assert_condition_time(alert: dict[str, Any], computed: float | None) -> None:
    assert computed is not None, f"{alert['rule_id']}: condition was never met"
    assert computed == pytest.approx(alert["t_condition"], abs=TOLERANCE_MIN), alert["rule_id"]
    assert alert["t_earliest"] <= computed <= alert["t_latest"], alert["rule_id"]


MAX_DURATION_MIN = 1800


def test_scenarios_fit_in_max_duration() -> None:
    for s in all_scenarios():
        assert s["duration_min"] <= MAX_DURATION_MIN, s["scenario"]


@pytest.mark.parametrize("name", ["hypoxia", "hypercapnia"])
def test_cabin_series_matches_atmosphere_engine(name: str) -> None:
    from dataclasses import replace

    from backend.atmosphere import CabinState, load_profile, metabolic_rates, step

    s = load(name)
    profile = load_profile(ROOT / "data" / "reference" / "ranges.json", "mars_transit")
    assert profile.volume_m3 == s["model"]["volume_m3"] == 75
    total = profile.total_pressure_mmhg
    state = CabinState(
        s["cabin_initial_pio2_mmHg"] * total / (total - profile.ranges.water_vapour_mmhg),
        s["cabin_initial"]["pp_co2"]["value"],
    )
    o2_fail = next((e["t_min"] for e in s["events"] if e["type"] == "o2_generation_rate"), None)
    co2_fail = next((e for e in s["events"] if e["type"] == "co2_removal_rate"), None)
    for t, pio2, pp_co2 in cabin_series(s):
        engine_pio2 = state.pp_o2_mmhg * (total - profile.ranges.water_vapour_mmhg) / total
        assert engine_pio2 == pytest.approx(pio2, abs=1e-6), (name, t)
        assert state.pp_co2_mmhg == pytest.approx(pp_co2, abs=1e-6), (name, t)
        rates = tuple(
            (vital(s, c["id"], "heart_rate", t), c["baseline"]["heart_rate"]["mean"]) for c in s["crew"]
        )
        used = [metabolic_rates(h, b, profile.ranges.metabolism) for h, b in rates]
        o2_failed = o2_fail is not None and t >= o2_fail
        active = replace(
            profile,
            o2_supply_kg_per_day=profile.o2_supply_kg_per_day if o2_failed else sum(u[0] for u in used),
            co2_removal_kg_per_day=(
                co2_fail["value"] * profile.co2_removal_kg_per_day
                if co2_fail and t >= co2_fail["t_min"]
                else sum(u[1] for u in used)
            ),
        )
        state = step(
            state, active, rates, STEP_MIN * 60, o2_supply_failed=o2_fail is not None and t >= o2_fail
        ).state


def test_required_scenarios_exist() -> None:
    assert {p.stem for p in SCENARIO_FILES} >= REQUIRED_SCENARIOS


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_scenario_format(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    assert s["scenario"] == path.stem, "file name and scenario field must match (AGENTS.md §5)"
    assert s["scenario"] in {sc.value for sc in m.Scenario}
    assert s["simulated"] is True and s["seed"] == 42
    assert s["thresholds_from"] == "data/reference/ranges.json"

    crew_ids = [c["id"] for c in s["crew"]]
    assert crew_ids == [f"crew-{i}" for i in range(1, m.CREW_SIZE + 1)]
    for c in s["crew"]:
        assert set(c["baseline"]) == CREW_METRICS, c["id"]
    assert set(s["cabin_initial"]) == CABIN_METRICS

    for e in s["events"]:
        assert 0 <= e["t_min"] <= s["duration_min"], e
        assert e.get("crew_id") in {*crew_ids, None}, e
        assert set(e.get("targets", {})) <= CREW_METRICS, e

    for a in s["expected_alerts"]:
        assert a["severity"] in SEVERITIES
        assert a["t_earliest"] <= a["t_condition"] <= a["t_latest"] <= s["duration_min"], a["rule_id"]
        assert a["why"] and a["recommendation"] and a["evidence_metrics"]
        assert a["rule_id"] in s["proposed_rules"], a["rule_id"]
        allowed = CABIN_METRICS if a["crew_id"] is None else CREW_METRICS | CREW_ALERT_EXTRA_METRICS
        assert a["metric"] in allowed, a
        assert set(a["evidence_metrics"]) <= CREW_METRICS | CABIN_METRICS | CREW_ALERT_EXTRA_METRICS

    assert set(s["expected_status_end"]) == {*crew_ids, "cabin"}
    assert set(s["expected_status_end"].values()) <= STATUSES


def test_shared_inputs_are_identical_across_scenarios() -> None:
    ref = load("hypoxia")
    for s in all_scenarios():
        for key in ("crew", "cabin_initial", "model", "thresholds", "ranges_refs"):
            assert s[key] == ref[key], f"{s['scenario']}: {key}"


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_expected_and_forbidden_do_not_overlap(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    expected = {(a["crew_id"], a["metric"]) for a in s["expected_alerts"]}
    forbidden = {(f["crew_id"], f["metric"]) for f in s.get("forbidden_alerts", [])}
    assert not expected & forbidden


ENGINE_TIERS = {"nominal", "caution", "critical", "unknown"}


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_tiers_match_engine(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    assert {a["severity"] for a in s["expected_alerts"]} <= ENGINE_TIERS - {"nominal", "unknown"}
    assert set(s["expected_status_end"].values()) <= ENGINE_TIERS
    assert not [k for k in s["proposed_rules"] if k.endswith("_warning")]


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_resting_targets_of_forbidden_metrics_stay_below_rest_rules(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    forbidden = {(f["crew_id"], f["metric"]) for f in s.get("forbidden_alerts", [])}
    rule_metrics = {r["metric"] for r in s["proposed_rules"].values() if isinstance(r, dict)}
    for e in s["events"]:
        if e.get("activity") == "exercise" or not e.get("crew_id"):
            continue
        for metric, target in e.get("targets", {}).items():
            if (e["crew_id"], metric) not in forbidden or metric not in REST_DELTA_KEYS:
                continue
            if metric not in rule_metrics:
                continue
            delta = abs(target - baseline(s, e["crew_id"], metric))
            limit = s["thresholds"][REST_DELTA_KEYS[metric]]
            assert delta < limit, f"t={e['t_min']} {e['crew_id']} {metric}={target} (fark {delta}) ama yasak"


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_crew_rules_using_cabin_evidence_declare_cabin_condition(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    for a in s["expected_alerts"]:
        if a["crew_id"] and set(a["evidence_metrics"]) & CABIN_METRICS:
            assert "also_requires" in s["proposed_rules"][a["rule_id"]], a["rule_id"]


@pytest.fixture(scope="module")
def ranges() -> dict[str, Any]:
    if not RANGES_PATH.exists():
        pytest.skip("data/reference/ranges.json is not on develop yet (PR #16)")
    return json.loads(RANGES_PATH.read_text(encoding="utf-8"))


def resolve(data: dict[str, Any], dotted: str) -> Any:
    for key in dotted.split("."):
        data = data[key]
    return data


def test_thresholds_match_ranges(ranges: dict[str, Any]) -> None:
    for s in all_scenarios():
        for name, dotted in s["ranges_refs"].items():
            assert s["thresholds"][name] == resolve(ranges, dotted), f"{s['scenario']}: {name} ({dotted})"


def test_baselines_and_profile_match_ranges(ranges: dict[str, Any]) -> None:
    s = load("hypoxia")
    keys = {"heart_rate": "nabiz", "spo2": "spo2", "resp_rate": "solunum", "core_temp": "isi"}
    for row in ranges["vital_karar_parametreleri"]["baz_cizgi"]["astronotlar"]:
        for metric, key in keys.items():
            assert baseline(s, f"crew-{row['id']}", metric) == row[key]
    profile = ranges["profiller"]["mars_transit"]
    md = s["model"]
    assert (md["volume_m3"], md["crew_count"]) == (profile["hacim_m3"], profile["kisi"])
    assert md["co2_removal_kg_day"] == profile["co2_giderme_kg_gun"]
    met = ranges["metabolik"]
    assert (md["o2_min"], md["o2_max"]) == (met["o2_tuketimi"]["min"], met["o2_tuketimi"]["max"])
    assert (md["co2_min"], md["co2_max"]) == (met["co2_uretimi"]["min"], met["co2_uretimi"]["max"])


def test_initial_pio2_is_inside_normoxia() -> None:
    s = load("hypoxia")
    th = s["thresholds"]
    assert th["pio2_normoxia_min"] <= s["cabin_initial_pio2_mmHg"] <= th["pio2_normoxia_max"]
    pp_o2 = s["cabin_initial_pio2_mmHg"] * 760 / (760 - WATER_VAPOR_MMHG)
    assert s["cabin_initial"]["pp_o2"]["unit"] == "mmHg"
    assert s["cabin_initial"]["pp_o2"]["value"] == pytest.approx(pp_o2, abs=0.05)
    assert s["cabin_initial"]["cabin_pressure"]["unit"] == "mmHg"


def test_nominal_has_no_alerts_even_with_exercise() -> None:
    s = load("nominal")
    assert s["expected_alerts"] == [] and s["no_alerts_at_all"] is True
    assert set(s["expected_status_end"].values()) == {"nominal"}
    exercise = next(e for e in s["events"] if e.get("activity") == "exercise")
    forbidden = {(f["crew_id"], f["metric"]) for f in s["forbidden_alerts"]}
    assert {(exercise["crew_id"], "heart_rate"), (exercise["crew_id"], "resp_rate")} <= forbidden


@pytest.fixture(scope="module")
def hypoxia() -> Scenario:
    return load("hypoxia")


@pytest.fixture(scope="module")
def hypoxia_cabin(hypoxia: Scenario) -> list[tuple[float, float, float]]:
    return cabin_series(hypoxia)


def test_hypoxia_cabin_alert_times(
    hypoxia: Scenario, hypoxia_cabin: list[tuple[float, float, float]]
) -> None:
    th = hypoxia["thresholds"]
    t145 = next(t for t, pio2, _ in hypoxia_cabin if pio2 < th["pio2_normoxia_min"])
    t127 = next(t for t, pio2, _ in hypoxia_cabin if pio2 < th["pio2_red"])
    assert_condition_time(by_rule(hypoxia, "cabin_pio2_low_caution"), t145)
    assert_condition_time(by_rule(hypoxia, "cabin_pio2_low_critical"), t127)


def test_hypoxia_crew3_exercising_when_spo2_drops(
    hypoxia: Scenario, hypoxia_cabin: list[tuple[float, float, float]]
) -> None:
    th = hypoxia["thresholds"]
    base = baseline(hypoxia, "crew-3", "spo2")
    caution = by_rule(hypoxia, "crew_hypoxia_caution")
    warning = by_rule(hypoxia, "crew_hypoxia_critical")
    for alert, key in ((caution, "spo2_drop_caution"), (warning, "spo2_drop_warning")):
        t = first_time(hypoxia, lambda t, k=key: base - vital(hypoxia, "crew-3", "spo2", t) >= th[k] - 1e-9)
        assert_condition_time(alert, t)
        pio2_now = next(p for tt, p, _ in hypoxia_cabin if tt >= alert["t_condition"])
        assert pio2_now < th["pio2_normoxia_min"]

    exercise_start = next(
        e["t_min"]
        for e in hypoxia["events"]
        if e.get("crew_id") == "crew-3" and e.get("activity") == "exercise"
    )
    exercise_end = next(
        e["t_min"]
        for e in hypoxia["events"]
        if e.get("crew_id") == "crew-3" and e.get("activity") == "resting" and e["t_min"] > exercise_start
    )
    for alert in (caution, warning):
        assert (
            exercise_start <= alert["t_condition"] < exercise_end
        ), "crew-3 must be exercising at decision time"
    assert warning["t_latest"] < by_rule(hypoxia, "cabin_pio2_low_critical")["t_earliest"], "early warning"


def test_hypoxia_exercise_heart_rate_is_not_evidence(hypoxia: Scenario) -> None:
    forbidden = {(f["crew_id"], f["metric"]) for f in hypoxia["forbidden_alerts"]}
    assert {("crew-3", "heart_rate"), ("crew-3", "resp_rate")} <= forbidden
    for a in hypoxia["expected_alerts"]:
        if a["crew_id"] == "crew-3":
            assert not {"heart_rate", "resp_rate"} & set(a["evidence_metrics"])
            assert "pp_o2" in a["evidence_metrics"]
    guarded = {f["rule_id"] for f in hypoxia["forbidden_evidence"] if f["crew_id"] == "crew-3"}
    assert guarded == {"crew_hypoxia_caution", "crew_hypoxia_critical"}


def test_hypoxia_personal_heart_rate_deviation(hypoxia: Scenario) -> None:
    th = hypoxia["thresholds"]
    ramps = {e["crew_id"]: e for e in hypoxia["events"] if e.get("crew_id") in {"crew-1", "crew-4"}}
    final1 = ramps["crew-1"]["targets"]["heart_rate"]
    final4 = ramps["crew-4"]["targets"]["heart_rate"]
    assert final1 == final4, "targets must be equal; the difference comes only from the baseline"
    assert final1 - baseline(hypoxia, "crew-1", "heart_rate") >= th["hr_rise_caution"]
    assert final4 - baseline(hypoxia, "crew-4", "heart_rate") < th["hr_rise_caution"]

    base1 = baseline(hypoxia, "crew-1", "heart_rate")
    t = first_time(
        hypoxia, lambda t: vital(hypoxia, "crew-1", "heart_rate", t) - base1 >= th["hr_rise_caution"]
    )
    assert_condition_time(by_rule(hypoxia, "crew_hr_rest_rise_caution"), t)
    assert ("crew-4", "heart_rate") in {(f["crew_id"], f["metric"]) for f in hypoxia["forbidden_alerts"]}
    assert hypoxia["expected_status_end"]["crew-1"] == "caution"
    assert hypoxia["expected_status_end"]["crew-4"] == "nominal"


def final_activity(s: Scenario, crew_id: str) -> str:
    acts = [
        e["activity"] for e in sorted(s["events"], key=lambda e: e["t_min"]) if e.get("crew_id") == crew_id
    ]
    return acts[-1] if acts else "resting"


def test_hypoxia_end_status_follows_rules(hypoxia: Scenario) -> None:
    th = hypoxia["thresholds"]
    end = hypoxia["duration_min"]
    pio2_end = cabin_series(hypoxia)[-1][1]
    assert pio2_end < th["pio2_normoxia_min"]
    order = ["nominal", "caution", "critical"]
    for c in hypoxia["crew"]:
        cid = c["id"]

        def delta(metric: str, crew: str = cid) -> float:
            return vital(hypoxia, crew, metric, end) - baseline(hypoxia, crew, metric)

        resting = final_activity(hypoxia, cid) != "exercise"
        drop, hr, rr = -delta("spo2"), delta("heart_rate"), delta("resp_rate")
        rest_sign = hr >= th["hr_rise_caution"] or rr >= th["rr_rise_caution"]
        levels = ["nominal"]
        if drop >= th["spo2_drop_warning"]:
            levels.append("critical")
        elif drop >= th["spo2_drop_caution"] and (rest_sign or not resting):
            levels.append("caution")
        if resting and hr >= th["hr_rise_warning"]:
            levels.append("critical")
        elif resting and hr >= th["hr_rise_caution"]:
            levels.append("caution")
        computed = max(levels, key=order.index)
        assert (
            hypoxia["expected_status_end"][cid] == computed
        ), f"{cid}: drop {drop}, pulse {hr}, respiration {rr}"


def test_hypercapnia_alert_times() -> None:
    s = load("hypercapnia")
    th = s["thresholds"]
    series = cabin_series(s)
    t_yellow = next(t for t, _, co2 in series if co2 > th["ppco2_yellow"])
    assert_condition_time(by_rule(s, "cabin_pp_co2_caution"), t_yellow)

    window = s["proposed_rules"]["cabin_pp_co2_critical"]["average_window_min"]
    per_window = int(window / STEP_MIN)
    t_avg = next(
        series[i][0]
        for i in range(per_window, len(series))
        if sum(c for _, _, c in series[i - per_window : i + 1]) / (per_window + 1) > th["ppco2_red_1h"]
    )
    assert_condition_time(by_rule(s, "cabin_pp_co2_critical"), t_avg)

    symptom = next(e for e in s["events"] if e.get("crew_id") == "crew-3")
    assert symptom["activity"] == "resting"
    co2_at_symptom = next(c for t, _, c in series if t >= symptom["t_min"])
    assert co2_at_symptom >= 2.8, "symptom must start in the NASA headache range"

    base_rr = baseline(s, "crew-3", "resp_rate")
    t_rr = first_time(
        s, lambda t: vital(s, "crew-3", "resp_rate", t) - base_rr >= th["rr_rise_caution"] - 1e-9
    )
    assert_condition_time(by_rule(s, "crew_co2_effect_caution"), t_rr)
    assert t_yellow < t_rr
    assert {a["crew_id"] for a in s["expected_alerts"] if a["crew_id"]} == {"crew-3"}


def test_solar_storm_events_carry_numeric_dose_fields() -> None:
    s = load("solar_storm")
    v = s["dose_assumptions"]
    onset = next(e for e in s["events"] if e["type"] == "sep_onset")
    shelter = next(e for e in s["events"] if e["type"] == "shelter")
    assert isinstance(onset["dose_rate_uGy_h"], int | float)
    assert onset["dose_rate_uGy_h"] == v["event_absorbed_dose_rate_uGy_per_h"]
    assert isinstance(shelter["dose_rate_multiplier"], int | float)
    assert shelter["dose_rate_multiplier"] == v["shelter_factor"]


def test_solar_storm_warns_before_dose_and_totals() -> None:
    s = load("solar_storm")
    v = s["dose_assumptions"]
    th = s["thresholds"]
    ev = {e["type"]: e["t_min"] for e in s["events"]}
    rate = v["event_effective_dose_mSv_per_h"] / 60

    projected = rate * v["event_duration_min"]
    assert (
        projected >= th["yellow_fraction"] * th["spe_design_mSv"]
    ), "projection must trigger the alert at onset"
    warning = by_rule(s, "event_dose_projection_critical")
    assert warning["t_condition"] == ev["sep_onset"]
    assert ev["shelter"] >= warning["t_condition"] and ev["shelter"] <= warning["t_latest"]
    dose_at_warning = rate * (warning["t_condition"] - ev["sep_onset"])
    assert dose_at_warning < 0.1 * th["spe_design_mSv"], "alert must come before the dose is received"

    total = rate * (ev["shelter"] - ev["sep_onset"]) + rate * v["shelter_factor"] * (
        ev["sep_end"] - ev["shelter"]
    )
    assert total == pytest.approx(s["expected_dose"]["event_total_mSv"], abs=0.2)
    assert total < th["spe_design_mSv"]
    background = v["gcr_mSv_per_day"] * (v["mission_day"] + s["duration_min"] / MINUTES_PER_DAY)
    worst = (max(v["prior_career_dose_mSv"].values()) + background + total) / th["career_limit_mSv"]
    assert worst == pytest.approx(s["expected_dose"]["max_career_fraction"], abs=0.01)
    assert worst < th["yellow_fraction"]


def test_solar_storm_matches_saved_donki_event() -> None:
    s = load("solar_storm")
    saved_path = ROOT / "data" / "donki" / "sep_event.json"
    if not saved_path.exists():
        pytest.skip("data/donki/sep_event.json is not on this branch (arrives when PR #17 merges)")
    selected = json.loads(saved_path.read_text(encoding="utf-8"))["selected"]
    assert s["donki"]["sepID"] == selected["sepID"]
    assert s["donki"]["event_time"] == selected["eventTime"]
    assert s["donki"]["linked_flare"] in [e["activityID"] for e in selected["linkedEvents"]]
    assert s["donki"]["linked_events_count"] == len(selected["linkedEvents"])
    names = [i["displayName"] for i in selected["instruments"]]
    assert s["donki"]["instrument"] in names
    assert not s["donki"]["instrument"].startswith("MODEL:"), "scenario must be based on a measured event"


REQUIRED_CONSECUTIVE = 3


def first_fire_index(flags: list[bool], n: int) -> int | None:
    run = 0
    for i, flag in enumerate(flags):
        run = run + 1 if flag else 0
        if run == n:
            return i
    return None


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_alerts_need_three_consecutive_measurements(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    interval = s["model"]["measurement_interval_min"]
    for a in s["expected_alerts"]:
        rule = s["proposed_rules"][a["rule_id"]]
        assert rule["consecutive_required"] == REQUIRED_CONSECUTIVE, a["rule_id"]
        fires = a["t_condition"] + (REQUIRED_CONSECUTIVE - 1) * interval
        assert fires <= a["t_latest"], f"{a['rule_id']}: 3rd measurement is outside the window"


def test_single_blip_does_not_fire_but_sustained_condition_does() -> None:
    s = load("hypoxia")
    interval = s["model"]["measurement_interval_min"]
    low = s["thresholds"]["pio2_normoxia_min"]
    sampled = [p for t, p, _ in cabin_series(s) if t % interval == 0]
    flags = [p < low for p in sampled]
    fired = first_fire_index(flags, REQUIRED_CONSECUTIVE)
    assert fired is not None
    assert fired - flags.index(True) == REQUIRED_CONSECUTIVE - 1
    blip = [False, True, True, False, False, True, False]
    assert first_fire_index(blip, REQUIRED_CONSECUTIVE) is None
    assert first_fire_index([False, True, True, True], REQUIRED_CONSECUTIVE) == 3


def exercise_intervals(s: Scenario, crew_id: str) -> list[tuple[float, float]]:
    events = sorted(
        (e for e in s["events"] if e.get("crew_id") == crew_id and "activity" in e),
        key=lambda e: e["t_min"],
    )
    spans: list[tuple[float, float]] = []
    start: float | None = None
    for e in events:
        if e["activity"] == "exercise" and start is None:
            start = e["t_min"]
        elif e["activity"] != "exercise" and start is not None:
            spans.append((start, e["t_min"]))
            start = None
    if start is not None:
        spans.append((start, float(s["duration_min"])))
    return spans


def resting_rise_flags(s: Scenario, crew_id: str, activity_of: Callable[[float], str]) -> list[bool]:
    th = s["thresholds"]["hr_rise_caution"]
    base = baseline(s, crew_id, "heart_rate")
    return [
        activity_of(t) == "resting" and vital(s, crew_id, "heart_rate", t) - base >= th - 1e-9
        for t in range(0, int(s["duration_min"]) + 1)
    ]


def test_exercise_heart_rate_never_raises_heart_load_alert() -> None:
    s = load("hypoxia")
    spans = exercise_intervals(s, "crew-3")
    assert spans, "crew-3 must exercise in the scenario"
    base = baseline(s, "crew-3", "heart_rate")
    th = s["thresholds"]["hr_rise_caution"]

    def activity_of(t: float) -> str:
        return "exercise" if any(a <= t < b for a, b in spans) else "resting"

    start, end = (int(x) for x in spans[0])
    peak = max(vital(s, "crew-3", "heart_rate", t) for t in range(start, end))
    assert peak - base >= th, "exercise pulse must exceed the threshold so the exception is meaningful"
    flags = resting_rise_flags(s, "crew-3", activity_of)
    assert not any(any(flags[int(a) : int(b)]) for a, b in spans)
    assert not any(a["crew_id"] == "crew-3" and a["metric"] == "heart_rate" for a in s["expected_alerts"])


def test_unknown_activity_never_counts_heart_rate_as_evidence() -> None:
    s = load("hypoxia")
    known = resting_rise_flags(s, "crew-1", lambda t: "resting")
    unknown = resting_rise_flags(s, "crew-1", lambda t: "unknown")
    assert first_fire_index(known, REQUIRED_CONSECUTIVE) is not None, "an alert must appear while resting"
    assert not any(unknown)
    assert first_fire_index(unknown, REQUIRED_CONSECUTIVE) is None


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_resting_heart_rate_rules_require_resting_and_skip_exercise(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    for rule_id, rule in s["proposed_rules"].items():
        if not isinstance(rule, dict) or not rule_id.startswith("crew_hr_rest_"):
            continue
        assert rule["condition"].startswith("activity = resting"), rule_id
        assert "exercise" in rule["skip_when"], rule_id


def test_missing_data_is_unknown_never_nominal() -> None:
    from backend.atmosphere import CabinState, load_profile, step

    s = load("hypoxia")
    profile = load_profile(RANGES_PATH, "mars_transit")
    total = profile.total_pressure_mmhg
    pp_o2 = s["cabin_initial_pio2_mmHg"] * total / (total - profile.ranges.water_vapour_mmhg)
    rates = tuple((c["baseline"]["heart_rate"]["mean"],) * 2 for c in s["crew"])
    pp_co2 = s["cabin_initial"]["pp_co2"]["value"]

    assert step(CabinState(pp_o2, pp_co2), profile, rates, 300).status == "nominal"
    assert step(CabinState(None, pp_co2), profile, rates, 300).status == "unknown"
    assert step(CabinState(pp_o2, None), profile, rates, 300).status == "unknown"
    missing_vitals = ((None, None), *rates[1:])
    assert step(CabinState(pp_o2, pp_co2), profile, missing_vitals, 300).status == "unknown"
    hypoxic = profile.pp_o2_from_pio2(s["thresholds"]["pio2_red"] - 1)
    assert step(CabinState(hypoxic, None), profile, rates, 300).status == "critical"


def vitals_all_missing() -> m.CrewVitals:
    from datetime import UTC, datetime

    ts = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    missing = m.Reading(value=None, unit="x", ts=ts, quality=m.Quality.missing)
    return m.CrewVitals(**{f: missing for f in m.CrewVitals.model_fields})


@pytest.mark.skipif(not hasattr(m, "CrewDecision"), reason="CrewDecision arrives in the contract with #26")
def test_contract_rejects_normal_decision_when_all_vitals_missing() -> None:
    with pytest.raises(ValueError):
        m.CrewMember(
            id="crew-1",
            name="A",
            role="Pilot",
            vitals=vitals_all_missing(),
            decision=m.CrewDecision.normal,
        )


@pytest.mark.skipif(
    "baseline_delta" not in m.Reading.model_fields, reason="baseline_delta arrives in the contract with #26"
)
def test_contract_rejects_baseline_delta_on_cabin_readings() -> None:
    from datetime import UTC, datetime

    ts = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    required = [f for f, field in m.CabinAtmosphere.model_fields.items() if field.is_required()]
    fields = {f: m.Reading(value=1.0, unit="x", ts=ts) for f in required}
    fields["pp_co2"] = m.Reading(value=1.0, unit="x", ts=ts, baseline_delta=0.5)
    with pytest.raises(ValueError):
        m.CabinAtmosphere(**fields)


@pytest.fixture(scope="module")
def dropout() -> Scenario:
    return load("sensor_dropout")


def quality_events(s: Scenario) -> list[dict[str, Any]]:
    return [e for e in s["events"] if e["type"] == "sensor_quality"]


def test_sensor_dropout_events_are_well_formed(dropout: Scenario) -> None:
    qualities = {q.value for q in m.Quality} - {m.Quality.ok.value}
    events = quality_events(dropout)
    assert {
        e["quality"] for e in events
    } == qualities, "missing, stale and sensor_fault must all be exercised"
    for e in events:
        assert 0 <= e["t_min"] < e["until_min"] <= dropout["duration_min"], e
        allowed = CABIN_METRICS if e["crew_id"] is None else CREW_METRICS
        assert e["metric"] in allowed, e
        assert ("stale_value" in e) == (e["quality"] == "stale"), e
        assert ("reported_value" in e) == (e["quality"] == "sensor_fault"), e


def test_sensor_dropout_status_table_matches_events(dropout: Scenario) -> None:
    events = {(e["crew_id"], e["metric"]): e for e in quality_events(dropout)}
    table = {(d["crew_id"], d["metric"]): d for d in dropout["expected_data_status"]}
    assert table.keys() == events.keys()
    for key, d in table.items():
        e = events[key]
        assert (d["t_from"], d["t_until"], d["quality"]) == (e["t_min"], e["until_min"], e["quality"]), key
        assert d["status"] == "unknown" and "nominal" in d["forbidden_status"], key
        assert d["recovery"]["status"] == "nominal", key
        assert d["t_until"] <= d["recovery"]["t_earliest"] <= d["recovery"]["t_latest"], key
        assert d["recovery"]["t_latest"] <= dropout["duration_min"], key


def test_sensor_dropout_expectations_are_valid_contract_readings(dropout: Scenario) -> None:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    for d in dropout["expected_data_status"]:
        fields = {"value": d["value"], "unit": "x", "ts": ts, "quality": d["quality"]}
        assert m.Reading(**fields, status=d["status"]).status == "unknown"
        with pytest.raises(ValidationError):
            m.Reading(**fields, status="nominal")


def missing_alerts(s: Scenario) -> list[dict[str, Any]]:
    return [a for a in s["expected_alerts"] if a["rule_id"].startswith("missing-")]


def test_sensor_dropout_no_data_alert_times(dropout: Scenario) -> None:
    n = dropout["thresholds"]["consecutive"]
    step = dropout["model"]["measurement_interval_min"]
    alerts = {(a["crew_id"], a["metric"]): a for a in missing_alerts(dropout)}
    statuses = {(d["crew_id"], d["metric"]): d for d in dropout["expected_data_status"]}
    assert alerts.keys() == statuses.keys(), "exactly one 'no data' alert for each faulty sensor"
    for key, alert in alerts.items():
        d = statuses[key]
        computed = d["t_from"] + (n - 1) * step
        assert computed == pytest.approx(alert["t_condition"], abs=TOLERANCE_MIN), key
        assert alert["t_earliest"] <= computed <= alert["t_latest"] <= dropout["duration_min"], key
        assert computed < d["t_until"], "the alert must appear before the dropout ends"
        assert alert["severity"] == "caution", "missing data is not a limit exceedance"
        assert alert["rule_id"] == f"missing-{d['metric']}"
        assert alert["evidence_metrics"] == [d["metric"]]


def test_sensor_dropout_fault_value_would_trip_the_limit_if_evaluated(dropout: Scenario) -> None:
    fault = next(e for e in quality_events(dropout) if e["quality"] == "sensor_fault")
    assert fault["reported_value"] > dropout["thresholds"]["ppco2_red_1h"], "the test would lose its meaning"
    assert fault["reported_value"] > 4 * dropout["cabin_initial"]["pp_co2"]["value"], "impossible jump"
    d = next(d for d in dropout["expected_data_status"] if d["crew_id"] is None)
    assert d["value"] is None, "the sensor_fault value must not enter the rules engine"
    assert {"nominal", "critical"} <= set(d["forbidden_status"])
    cabin = next(f for f in dropout["forbidden_rules"] if f["crew_id"] is None and f["metric"] == "pp_co2")
    assert {"cabin_pp_co2_critical"} <= set(cabin["rule_ids"])
    assert [a["rule_id"] for a in dropout["expected_alerts"] if a["crew_id"] is None] == ["missing-pp_co2"]


def test_sensor_dropout_forbidden_rules_do_not_overlap_expected(dropout: Scenario) -> None:
    expected = {(a["crew_id"], a["metric"], a["rule_id"]) for a in dropout["expected_alerts"]}
    for f in dropout["forbidden_rules"]:
        assert f["rule_ids"], f
        for rule_id in f["rule_ids"]:
            assert (f["crew_id"], f["metric"], rule_id) not in expected, (f["crew_id"], rule_id)
    forbidden_keys = {(f["crew_id"], f["metric"]) for f in dropout["forbidden_rules"]}
    assert forbidden_keys == {(d["crew_id"], d["metric"]) for d in dropout["expected_data_status"]}


def test_sensor_dropout_crew3_target_has_margin_over_sensor_noise(dropout: Scenario) -> None:
    th = dropout["thresholds"]
    crew3 = next(c for c in dropout["crew"] if c["id"] == "crew-3")["baseline"]["heart_rate"]
    target = next(
        e["targets"]["heart_rate"]
        for e in dropout["events"]
        if e.get("crew_id") == "crew-3" and "heart_rate" in e.get("targets", {})
    )
    assert target - crew3["mean"] >= th["hr_rise_caution"] + 2 * crew3["sd"]


def test_sensor_dropout_does_not_hide_another_persons_alert(dropout: Scenario) -> None:
    th = dropout["thresholds"]
    base = baseline(dropout, "crew-3", "heart_rate")
    t = first_time(
        dropout, lambda t: vital(dropout, "crew-3", "heart_rate", t) - base >= th["hr_rise_caution"]
    )
    alert = by_rule(dropout, "crew_hr_rest_rise_caution")
    assert_condition_time(alert, t)
    assert final_activity(dropout, "crew-3") == "resting"
    gap = next(e for e in quality_events(dropout) if e["crew_id"] == "crew-2")
    assert gap["t_min"] <= alert["t_condition"] < gap["until_min"], "the alert must arise during the dropout"
    assert alert["t_latest"] > gap["t_min"]
    assert dropout["expected_status_end"]["crew-3"] == "caution"
    assert ("crew-3", "heart_rate") not in {(f["crew_id"], f["metric"]) for f in dropout["forbidden_alerts"]}


def test_sensor_dropout_end_status_after_recovery(dropout: Scenario) -> None:
    end = dropout["expected_status_end"]
    recovered = {d["crew_id"] or "cabin" for d in dropout["expected_data_status"]}
    assert all(end[who] == "nominal" for who in recovered), "must return to Normal when data comes back"
    assert set(end.values()) <= {"nominal", "caution"} and "unknown" not in end.values()


def test_sensor_dropout_cabin_stays_nominal_without_the_fault(dropout: Scenario) -> None:
    th = dropout["thresholds"]
    series = cabin_series(dropout)
    assert all(th["pio2_normoxia_min"] <= pio2 <= th["pio2_normoxia_max"] for _, pio2, _ in series)
    assert all(co2 < th["ppco2_yellow"] for _, _, co2 in series)
    assert dropout["expected_status_end"]["cabin"] == "nominal"


NOISE_SD_FACTOR = 2.0

MARGIN_DEMO_EXCEPTIONS = {("hypoxia", "crew-1", "heart_rate"), ("hypoxia", "crew-4", "heart_rate")}


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_targets_keep_two_sd_margin_from_thresholds(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    th = s["thresholds"]
    sd = {c["id"]: {k: v["sd"] for k, v in c["baseline"].items()} for c in s["crew"]}
    expects = {(a["crew_id"], a["metric"]) for a in s["expected_alerts"]}
    for e in s["events"]:
        crew_id, targets = e.get("crew_id"), e.get("targets", {})
        if not targets or e.get("activity") != "resting":
            continue
        for metric, key in (("heart_rate", "hr_rise_caution"), ("resp_rate", "rr_rise_caution")):
            if metric not in targets:
                continue
            if (s["scenario"], crew_id, metric) in MARGIN_DEMO_EXCEPTIONS:
                continue
            delta = targets[metric] - baseline(s, crew_id, metric)
            margin = NOISE_SD_FACTOR * sd[crew_id][metric]
            if (crew_id, metric) in expects:
                assert delta >= th[key] + margin, (s["scenario"], crew_id, metric, delta)
            elif metric == "heart_rate":
                assert delta <= th[key] - margin, (s["scenario"], crew_id, metric, delta)


KNOWN_ENGINE_RULE_IDS = {
    "karar-olasi_hipoksi",
    "karar-olasi_co2_etkisi",
    "karar-isi_sapmasi",
    "karar-kalp_yuku",
    "karar-radyasyon_etkisi",
    "ppco2-caution",
    "ppco2-limit",
    "pio2-normoxia-low",
    "pio2-hypoxia-low",
    "pio2-normoxia-high",
}


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_expected_alerts_name_their_engine_counterpart(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    for a in s["expected_alerts"]:
        assert a["engine_metric"] in m.Metric.__members__, a["rule_id"]
        known = a["engine_rule_id"] in KNOWN_ENGINE_RULE_IDS or a["engine_rule_id"].startswith("missing-")
        assert known, (a["rule_id"], a["engine_rule_id"])


def test_solar_storm_radiation_is_a_per_crew_decision() -> None:
    s = load("solar_storm")
    alerts = s["expected_alerts"]
    assert {a["crew_id"] for a in alerts} == {c["id"] for c in s["crew"]}
    for a in alerts:
        assert (a["metric"], a["severity"], a["engine_rule_id"]) == (
            "event_dose",
            "critical",
            "karar-radyasyon_etkisi",
        )
        assert (a["t_earliest"], a["t_latest"]) == (30, 45)
    assert not any(a["crew_id"] is None for a in alerts)
    assert not any(a["metric"] == "radiation_dose_rate" for a in alerts)


try:
    from backend.rules import RulesEngine, SepContext
    from backend.simulator import SimulationFinished, Simulator
except ImportError:
    RulesEngine = None


def engine_run(name: str) -> tuple[dict[tuple[Any, str, str, str], float], Any]:
    simulator, engine = Simulator(m.Scenario(name)), RulesEngine()
    first: dict[tuple[Any, str, str, str], float] = {}
    last = None
    while True:
        try:
            frame = simulator.tick()
        except SimulationFinished:
            return first, last
        sep = simulator.sep_state
        sep_context = (
            SepContext(
                sep_active=True,
                event_dose_rate_msv_per_h=sep.event_dose_rate_msv_per_h,
                remaining_event_h=sep.remaining_event_h,
            )
            if sep.active
            else None
        )
        output = engine.evaluate(frame, sep=sep_context)
        last = output
        for alert in output.alerts:
            key = (alert.crew_id, str(alert.metric), str(alert.severity), alert.rule_id)
            first.setdefault(key, output.mission_elapsed_s / 60)


def engine_first_alerts(name: str) -> dict[tuple[Any, str, str, str], float]:
    return engine_run(name)[0]


@pytest.mark.skipif(RulesEngine is None, reason="simulator and rules engine are not on develop (#35, #43)")
@pytest.mark.skipif(RulesEngine is None, reason="simulator and rules engine are not on develop (#35, #43)")
@pytest.mark.parametrize("name", ["hypoxia", "hypercapnia", "solar_storm", "sensor_dropout"])
def test_engine_alerts_fall_inside_expected_windows(name: str) -> None:
    s = load(name)
    first = engine_first_alerts(name)
    for a in s["expected_alerts"]:
        key = (a["crew_id"], a["engine_metric"], a["severity"], a["engine_rule_id"])
        assert key in first, f"{name}: engine did not produce {key}"
        window = (a["t_earliest"], a["t_latest"])
        assert window[0] <= first[key] <= window[1], (name, a["rule_id"], first[key])
    forbidden = {(f["crew_id"], f["metric"]) for f in s["forbidden_alerts"]}
    produced = {(k[0], k[1]) for k in first}
    assert not forbidden & produced, f"{name}: forbidden alert produced: {forbidden & produced}"


@pytest.mark.skipif(RulesEngine is None, reason="simulator and rules engine are not on develop (#35, #43)")
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("nominal", id="nominal"),
        pytest.param("hypercapnia", id="hypercapnia"),
        pytest.param("solar_storm", id="solar_storm"),
        pytest.param("sensor_dropout", id="sensor_dropout"),
        pytest.param(
            "hypoxia",
            id="hypoxia",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "Known engine deviation (Aydın): (1) while crew-3 PIO2 < 127, olasi_hipoksi "
                    "becomes critical; the ranges.json action condition has no PIO2 term, only the SpO2 "
                    "drop. (2) crew-1 kalp_yuku flickers at the last measurement (pulse noise), "
                    "so the end state depends on the last frame."
                ),
            ),
        ),
    ],
)
def test_engine_end_status_matches_expected(name: str) -> None:
    s = load(name)
    _, last = engine_run(name)
    got = {c.id: getattr(c.status, "value", c.status) for c in last.crew}
    got["cabin"] = getattr(last.cabin.status, "value", last.cabin.status)
    assert got == s["expected_status_end"]


@pytest.mark.skipif(RulesEngine is None, reason="simulator and rules engine are not on develop (#35, #43)")
def test_hypoxia_engine_no_forbidden_heart_alerts() -> None:
    first = engine_first_alerts("hypoxia")
    assert ("crew-4", "heart_rate", "caution", "karar-kalp_yuku") not in first
    assert not any(
        k[0] == "crew-3" and k[1] == "heart_rate" for k in first
    ), "cardiac load is forbidden for Katherine"
    neil = next((t for k, t in first.items() if k[0] == "crew-1" and k[1] == "heart_rate"), None)
    assert neil is not None, "Neil must produce kalp_yuku"
    assert 172 <= neil <= 215, f"engine warned Neil at {neil} min; window [172, 215]"
