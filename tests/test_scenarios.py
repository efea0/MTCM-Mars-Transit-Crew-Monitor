"""Senaryo dosyaları (data/scenarios/*.json) için tutarlılık testleri — sahibi: Barış (qa).

simulator.py ve rules.py yazılmadan önce de çalışır. Denetlenenler:
1. Biçim: dosya adı = Scenario enum değeri; crew/metric/severity sözleşmeyle uyumlu.
2. Eşikler, baz çizgiler ve model parametreleri data/reference/ranges.json ile aynı (dosya varsa).
3. Beklenen karar tablosundaki koşul anları, senaryonun kendi olaylarından bağımsız bir kütle dengesi ve
   vital modeliyle yeniden hesaplanır. Biri bir sayıyı değiştirip tabloyu güncellemezse test kırılır.
4. Senaryo kendi içinde çelişmez: yasak bir uyarıyı tetikleyecek hedef değer, kanıt-kural uyumu.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from backend import models as m

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "data" / "scenarios"
RANGES_PATH = ROOT / "data" / "reference" / "ranges.json"
SCENARIO_FILES = sorted(SCENARIO_DIR.glob("*.json"))
REQUIRED_SCENARIOS = {"nominal", "hypoxia", "hypercapnia", "solar_storm"}

CREW_METRICS = set(m.CrewVitals.model_fields)
CABIN_METRICS = set(m.CabinAtmosphere.model_fields) - {"status"}
SEVERITIES = {s.value for s in m.Severity}
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


# ---------------------------------------------------------------- bağımsız model


def vital(s: Scenario, crew_id: str, metric: str, t: float) -> float:
    """Olaylardaki doğrusal rampalarla vital değeri (gürültüsüz)."""
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
    """(t, PIO2 mmHg, ppCO2 mmHg). Arızadan önce yaşam destek regüle (net 0)."""
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
        if o2_fail is not None and t >= o2_fail:
            pp_o2 -= o2_use * mmhg_per_kg(O2_MOLAR_G) * STEP_MIN / MINUTES_PER_DAY
        if co2_fail and t >= co2_fail["t_min"]:
            net = max(0.0, co2_make - co2_fail["value"] * md["co2_removal_kg_day"])
            pp_co2 += net * mmhg_per_kg(CO2_MOLAR_G) * STEP_MIN / MINUTES_PER_DAY
    return out


def assert_condition_time(alert: dict[str, Any], computed: float | None) -> None:
    assert computed is not None, f"{alert['rule_id']}: koşul hiç sağlanmadı"
    assert computed == pytest.approx(alert["t_condition"], abs=TOLERANCE_MIN), alert["rule_id"]
    assert alert["t_earliest"] <= computed <= alert["t_latest"], alert["rule_id"]


# ---------------------------------------------------------------- tüm senaryolar


def test_required_scenarios_exist() -> None:
    assert {p.stem for p in SCENARIO_FILES} >= REQUIRED_SCENARIOS


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_scenario_format(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    assert s["scenario"] == path.stem, "dosya adı ile scenario alanı aynı olmalı (AGENTS.md §5)"
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
        allowed = CABIN_METRICS if a["crew_id"] is None else CREW_METRICS
        assert a["metric"] in allowed, a
        assert set(a["evidence_metrics"]) <= CREW_METRICS | CABIN_METRICS

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


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_resting_targets_of_forbidden_metrics_stay_below_rest_rules(path: Path) -> None:
    """Dinlenen kişinin yasak listesindeki vitali dinlenme eşiğini (ranges.json izle farkı) geçmemeli.

    Örnek: egzersiz sonrası crew-3'ün nabzı bazın +15 üstüne çıkarsa kalp yükü uyarısı üretilir; oysa
    crew-3 heart_rate yasak. Dosya kendi içinde çelişmiş olur. Senaryoda o metrikle uyarı üreten kural
    yoksa (ör. hypoxia'da solunum yalnızca hipoksi kanıtı) sapma tek başına uyarı üretmez.
    """
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


# ---------------------------------------------------------------- ranges.json tek kaynak


@pytest.fixture(scope="module")
def ranges() -> dict[str, Any]:
    if not RANGES_PATH.exists():
        pytest.skip("data/reference/ranges.json henüz develop'ta yok (PR #16)")
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
    kpa = s["cabin_initial_pio2_mmHg"] * 760 / (760 - WATER_VAPOR_MMHG) * PA_PER_MMHG / 1000
    assert s["cabin_initial"]["pp_o2"]["value"] == pytest.approx(kpa, abs=0.01)


# ---------------------------------------------------------------- nominal


def test_nominal_has_no_alerts_even_with_exercise() -> None:
    s = load("nominal")
    assert s["expected_alerts"] == [] and s["no_alerts_at_all"] is True
    assert set(s["expected_status_end"].values()) == {"nominal"}
    exercise = next(e for e in s["events"] if e.get("activity") == "exercise")
    forbidden = {(f["crew_id"], f["metric"]) for f in s["forbidden_alerts"]}
    assert {(exercise["crew_id"], "heart_rate"), (exercise["crew_id"], "resp_rate")} <= forbidden


# ---------------------------------------------------------------- hypoxia


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
    assert_condition_time(by_rule(hypoxia, "cabin_pio2_low_warning"), t127)


def test_hypoxia_crew3_exercising_when_spo2_drops(
    hypoxia: Scenario, hypoxia_cabin: list[tuple[float, float, float]]
) -> None:
    th = hypoxia["thresholds"]
    base = baseline(hypoxia, "crew-3", "spo2")
    caution = by_rule(hypoxia, "crew_hypoxia_caution")
    warning = by_rule(hypoxia, "crew_hypoxia_warning")
    for alert, key in ((caution, "spo2_drop_caution"), (warning, "spo2_drop_warning")):
        t = first_time(hypoxia, lambda t, k=key: base - vital(hypoxia, "crew-3", "spo2", t) >= th[k] - 1e-9)
        assert_condition_time(alert, t)
        # kabin koşulu da o anda sağlanıyor
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
        assert exercise_start <= alert["t_condition"] < exercise_end, "karar anında crew-3 egzersizde olmalı"
    assert warning["t_latest"] < by_rule(hypoxia, "cabin_pio2_low_warning")["t_earliest"], "erken uyarı"


def test_hypoxia_exercise_heart_rate_is_not_evidence(hypoxia: Scenario) -> None:
    forbidden = {(f["crew_id"], f["metric"]) for f in hypoxia["forbidden_alerts"]}
    assert {("crew-3", "heart_rate"), ("crew-3", "resp_rate")} <= forbidden
    for a in hypoxia["expected_alerts"]:
        if a["crew_id"] == "crew-3":
            assert not {"heart_rate", "resp_rate"} & set(a["evidence_metrics"])
            assert "pp_o2" in a["evidence_metrics"]
    guarded = {f["rule_id"] for f in hypoxia["forbidden_evidence"] if f["crew_id"] == "crew-3"}
    assert guarded == {"crew_hypoxia_caution", "crew_hypoxia_warning"}


def test_hypoxia_personal_heart_rate_deviation(hypoxia: Scenario) -> None:
    """Aynı nabız, farklı karar: crew-1 ve crew-4 ikisi de 80; crew-1 İzle, crew-4 normal."""
    th = hypoxia["thresholds"]
    ramps = {e["crew_id"]: e for e in hypoxia["events"] if e.get("crew_id") in {"crew-1", "crew-4"}}
    assert ramps["crew-1"]["targets"]["heart_rate"] == ramps["crew-4"]["targets"]["heart_rate"]
    final = ramps["crew-1"]["targets"]["heart_rate"]
    assert final - baseline(hypoxia, "crew-1", "heart_rate") >= th["hr_rise_caution"]
    assert final - baseline(hypoxia, "crew-4", "heart_rate") < th["hr_rise_caution"]

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
    """Bitiş durumu, son vitallerden kurallarla yeniden hesaplanır.

    Hipoksi: SpO2 düşüşü ≥ izle VE kabin PIO2 < 145; dinlenen kişide ek olarak nabız veya solunum
    artışı ≥ izle. Eylem: SpO2 düşüşü ≥ eylem. Kalp yükü: dinlenirken nabız artışı ≥ izle / eylem.
    """
    th = hypoxia["thresholds"]
    end = hypoxia["duration_min"]
    pio2_end = cabin_series(hypoxia)[-1][1]
    assert pio2_end < th["pio2_normoxia_min"]
    order = ["nominal", "caution", "warning"]
    for c in hypoxia["crew"]:
        cid = c["id"]

        def delta(metric: str, crew: str = cid) -> float:
            return vital(hypoxia, crew, metric, end) - baseline(hypoxia, crew, metric)

        resting = final_activity(hypoxia, cid) != "exercise"
        drop, hr, rr = -delta("spo2"), delta("heart_rate"), delta("resp_rate")
        rest_sign = hr >= th["hr_rise_caution"] or rr >= th["rr_rise_caution"]
        levels = ["nominal"]
        if drop >= th["spo2_drop_warning"]:
            levels.append("warning")
        elif drop >= th["spo2_drop_caution"] and (rest_sign or not resting):
            levels.append("caution")
        if resting and hr >= th["hr_rise_warning"]:
            levels.append("warning")
        elif resting and hr >= th["hr_rise_caution"]:
            levels.append("caution")
        computed = max(levels, key=order.index)
        assert (
            hypoxia["expected_status_end"][cid] == computed
        ), f"{cid}: düşüş {drop}, nabız {hr}, solunum {rr}"


# ---------------------------------------------------------------- hypercapnia


def test_hypercapnia_alert_times() -> None:
    s = load("hypercapnia")
    th = s["thresholds"]
    series = cabin_series(s)
    t_yellow = next(t for t, _, co2 in series if co2 > th["ppco2_yellow"])
    assert_condition_time(by_rule(s, "cabin_pp_co2_caution"), t_yellow)

    window = s["proposed_rules"]["cabin_pp_co2_warning"]["average_window_min"]
    per_window = int(window / STEP_MIN)
    t_avg = next(
        series[i][0]
        for i in range(per_window, len(series))
        if sum(c for _, _, c in series[i - per_window : i + 1]) / (per_window + 1) > th["ppco2_red_1h"]
    )
    assert_condition_time(by_rule(s, "cabin_pp_co2_warning"), t_avg)

    symptom = next(e for e in s["events"] if e.get("crew_id") == "crew-3")
    assert symptom["activity"] == "resting"
    co2_at_symptom = next(c for t, _, c in series if t >= symptom["t_min"])
    assert co2_at_symptom >= 2.8, "belirti NASA baş ağrısı aralığında başlamalı"

    base_rr = baseline(s, "crew-3", "resp_rate")
    t_rr = first_time(
        s, lambda t: vital(s, "crew-3", "resp_rate", t) - base_rr >= th["rr_rise_caution"] - 1e-9
    )
    assert_condition_time(by_rule(s, "crew_co2_effect_caution"), t_rr)
    assert t_yellow < t_rr
    assert {a["crew_id"] for a in s["expected_alerts"] if a["crew_id"]} == {"crew-3"}


# ---------------------------------------------------------------- solar_storm


def test_solar_storm_warns_before_dose_and_totals() -> None:
    s = load("solar_storm")
    v = s["dose_assumptions"]
    th = s["thresholds"]
    ev = {e["type"]: e["t_min"] for e in s["events"]}
    rate = v["event_effective_dose_mSv_per_h"] / 60

    projected = rate * v["event_duration_min"]
    assert (
        projected >= th["yellow_fraction"] * th["spe_design_mSv"]
    ), "projeksiyon onset'te uyarıyı tetiklemeli"
    warning = by_rule(s, "event_dose_projection_warning")
    assert warning["t_condition"] == ev["sep_onset"]
    assert ev["shelter"] >= warning["t_condition"] and ev["shelter"] <= warning["t_latest"]
    dose_at_warning = rate * (warning["t_condition"] - ev["sep_onset"])
    assert dose_at_warning < 0.1 * th["spe_design_mSv"], "uyarı doz alınmadan önce gelmeli"

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
        pytest.skip("data/donki/sep_event.json bu dalda yok (PR #17 birleşince gelir)")
    selected = json.loads(saved_path.read_text(encoding="utf-8"))["selected"]
    assert s["donki"]["sepID"] == selected["sepID"]
    assert s["donki"]["event_time"] == selected["eventTime"]
    assert s["donki"]["linked_flare"] in [e["activityID"] for e in selected["linkedEvents"]]
    assert s["donki"]["linked_events_count"] == len(selected["linkedEvents"])
    names = [i["displayName"] for i in selected["instruments"]]
    assert s["donki"]["instrument"] in names
    assert not s["donki"]["instrument"].startswith("MODEL:"), "senaryo ölçülmüş olaya dayanmalı"
