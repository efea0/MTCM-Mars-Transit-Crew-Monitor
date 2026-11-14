"""Senaryo dosyaları (data/scenarios/*.json) için tutarlılık testleri — sahibi: Barış (qa).

simulator.py ve rules.py yazılmadan önce de çalışır. İki şeyi denetler:
1. Biçim: dosya adı = Scenario enum değeri, crew/metric/severity sözleşmeyle uyumlu, zamanlar mantıklı.
2. Beklenen karar tablosundaki zaman pencereleri, senaryodaki varsayımlardan yeniden hesaplanan anı kapsar.
   Biri bir sayıyı değiştirip tabloyu güncellemeyi unutursa test kırılır.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend import models as m

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "data" / "scenarios"
SCENARIO_FILES = sorted(SCENARIO_DIR.glob("*.json"))

CREW_METRICS = set(m.CrewVitals.model_fields)
CABIN_METRICS = set(m.CabinAtmosphere.model_fields) - {"status"}
SEVERITIES = {s.value for s in m.Severity}
STATUSES = {s.value for s in m.Status}

MMHG_PER_KPA = 7.50062
GAS_CONSTANT = 8.314
O2_MOLAR_KG = 0.032
CO2_MOLAR_KG = 0.04401
REST_RULE_Z = 2.0  # crew_hr_rest_z_caution ve benzeri dinlenme kurallarının en düşük eşiği
REQUIRED_SCENARIOS = {"nominal", "hypoxia", "hypercapnia", "solar_storm"}


def load(name: str) -> dict[str, Any]:
    return json.loads((SCENARIO_DIR / f"{name}.json").read_text(encoding="utf-8"))


def find_alert(s: dict[str, Any], crew_id: str | None, metric: str, severity: str) -> dict[str, Any]:
    for a in s["expected_alerts"]:
        if (a["crew_id"], a["metric"], a["severity"]) == (crew_id, metric, severity):
            return a
    raise AssertionError(f"beklenen uyarı yok: {crew_id} {metric} {severity}")


def assert_in_window(t: float, alert: dict[str, Any]) -> None:
    lo, hi = alert["t_earliest"], alert["t_latest"]
    assert lo <= t <= hi, f"{alert['rule_id']}: hesaplanan t={t:.1f}, pencere [{lo}, {hi}]"


def ramp_cross_time(event: dict[str, Any], baseline: dict[str, float], metric: str, z: float) -> float:
    """Doğrusal rampada metriğin baz + z·sd değerini geçtiği görev dakikası."""
    slope = (event["targets"][metric] - baseline["mean"]) / event["ramp_min"]
    return event["t_min"] + z * baseline["sd"] / slope


# ---------------------------------------------------------------- tüm senaryolar: biçim


def test_scenario_dir_has_files() -> None:
    assert SCENARIO_FILES, "data/scenarios/ boş"


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_scenario_format(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    assert s["scenario"] == path.stem, "dosya adı ile scenario alanı aynı olmalı (AGENTS.md §5)"
    assert s["scenario"] in {sc.value for sc in m.Scenario}
    assert s["simulated"] is True
    assert s["seed"] == 42, "demo tekrarlanabilir olmalı"

    crew_ids = [c["id"] for c in s["crew"]]
    assert crew_ids == [f"crew-{i}" for i in range(1, m.CREW_SIZE + 1)]
    for c in s["crew"]:
        assert set(c["baseline"]) == CREW_METRICS, c["id"]
        assert all(b["sd"] > 0 for b in c["baseline"].values())
    assert set(s["cabin_initial"]) == CABIN_METRICS

    for e in s["events"]:
        assert 0 <= e["t_min"] <= s["duration_min"], e
        assert e.get("crew_id") in {*crew_ids, None}, e
        assert set(e.get("targets", {})) <= CREW_METRICS, e

    for a in s["expected_alerts"]:
        assert a["severity"] in SEVERITIES
        assert a["t_earliest"] <= a["t_latest"] <= s["duration_min"]
        assert a["why"] and a["recommendation"], "her uyarının nedeni ve önerisi olmalı"
        assert a["evidence_metrics"], "Alert.why en az bir kanıt ister"
        assert a["rule_id"] in s["proposed_rules"], a["rule_id"]
        allowed = CABIN_METRICS if a["crew_id"] is None else CREW_METRICS
        assert a["metric"] in allowed, a
        assert a["crew_id"] in {*crew_ids, None}
        assert set(a["evidence_metrics"]) <= CREW_METRICS | CABIN_METRICS

    assert set(s["expected_status_end"]) == {*crew_ids, "cabin"}
    assert set(s["expected_status_end"].values()) <= STATUSES


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_expected_and_forbidden_do_not_overlap(path: Path) -> None:
    s = json.loads(path.read_text(encoding="utf-8"))
    expected = {(a["crew_id"], a["metric"]) for a in s["expected_alerts"]}
    forbidden = {(f["crew_id"], f["metric"]) for f in s.get("forbidden_alerts", [])}
    assert not expected & forbidden, f"hem bekleniyor hem yasak: {expected & forbidden}"


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_resting_targets_of_forbidden_metrics_stay_below_rest_rules(path: Path) -> None:
    """Dinlenen kişinin yasak listesindeki vitali dinlenme kuralını tetiklememeli (|z| < 2).

    Örnek: egzersiz sonrası toparlanmada crew-3'ün nabzı 80'e çıkarsa (z=3,4) crew_hr_rest_z_* kuralları
    nabız uyarısı üretir; oysa crew-3 heart_rate yasak. Dosya kendi içinde çelişmiş olur.
    """
    s = json.loads(path.read_text(encoding="utf-8"))
    base = {c["id"]: c["baseline"] for c in s["crew"]}
    forbidden = {(f["crew_id"], f["metric"]) for f in s.get("forbidden_alerts", [])}
    for e in s["events"]:
        if e.get("activity") == "exercise" or not e.get("crew_id"):
            continue
        for metric, target in e.get("targets", {}).items():
            if (e["crew_id"], metric) not in forbidden:
                continue
            b = base[e["crew_id"]][metric]
            z = (target - b["mean"]) / b["sd"]
            assert (
                abs(z) < REST_RULE_Z
            ), f"t={e['t_min']} {e['crew_id']} {metric}={target} z={z:+.2f} ama yasak"


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.stem)
def test_crew_rules_using_cabin_evidence_declare_cabin_condition(path: Path) -> None:
    """Kişi uyarısı kabin metriğini kanıt gösteriyorsa, kuralı da kabin koşulunu içermeli."""
    s = json.loads(path.read_text(encoding="utf-8"))
    for a in s["expected_alerts"]:
        if a["crew_id"] and set(a["evidence_metrics"]) & CABIN_METRICS:
            assert "also_requires" in s["proposed_rules"][a["rule_id"]], a["rule_id"]


# ---------------------------------------------------------------- hypoxia


@pytest.fixture(scope="module")
def hypoxia() -> dict[str, Any]:
    return load("hypoxia")


@pytest.fixture(scope="module")
def baselines(hypoxia: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    return {c["id"]: c["baseline"] for c in hypoxia["crew"]}


def o2_drop_kpa_per_hour(s: dict[str, Any]) -> float:
    v = s["assumptions"]
    kg_per_hour = v["crew_count"] * v["o2_consumption_kg_per_person_day"] / 24
    pa_per_kg = (1 / O2_MOLAR_KG) * GAS_CONSTANT * v["temperature_K"] / v["free_volume_m3"]
    return kg_per_hour * pa_per_kg / 1000


def kpa_time(s: dict[str, Any], level_kpa: float) -> float:
    fail = next(e for e in s["events"] if e["type"] == "o2_generation_rate")
    start = s["cabin_initial"]["pp_o2"]["value"]
    return fail["t_min"] + (start - level_kpa) / o2_drop_kpa_per_hour(s) * 60


def test_hypoxia_cabin_values_match_pio2(hypoxia: dict[str, Any]) -> None:
    v = hypoxia["assumptions"]
    dry = (v["total_pressure_mmHg"] - v["water_vapor_mmHg"]) / v["total_pressure_mmHg"]
    pio2_start = hypoxia["cabin_initial"]["pp_o2"]["value"] * MMHG_PER_KPA * dry
    pio2_limit = hypoxia["proposed_rules"]["cabin_pp_o2_low_warning"]["threshold"] * MMHG_PER_KPA * dry
    assert pio2_start == pytest.approx(149, abs=0.5)
    assert pio2_limit == pytest.approx(139, abs=0.5)


def test_hypoxia_cabin_alert_timings(hypoxia: dict[str, Any]) -> None:
    rules = hypoxia["proposed_rules"]
    t_caution = kpa_time(hypoxia, rules["cabin_pp_o2_low_caution"]["threshold"])
    t_warning = kpa_time(hypoxia, rules["cabin_pp_o2_low_warning"]["threshold"])
    assert_in_window(t_caution, find_alert(hypoxia, None, "pp_o2", "caution"))
    assert_in_window(t_warning, find_alert(hypoxia, None, "pp_o2", "warning"))
    assert_in_window(t_warning, find_alert(hypoxia, "crew-3", "spo2", "warning"))

    fail_t = next(e for e in hypoxia["events"] if e["type"] == "o2_generation_rate")["t_min"]
    countdown = hypoxia["expected_countdown"]
    computed = t_warning - (fail_t + 1)
    assert computed == pytest.approx(countdown["o2_min_at_t11"], rel=countdown["tolerance_pct"] / 100)


def test_hypoxia_crew3_exercising_when_spo2_drops(
    hypoxia: dict[str, Any], baselines: dict[str, dict[str, dict[str, float]]]
) -> None:
    exercise = next(
        e for e in hypoxia["events"] if e.get("crew_id") == "crew-3" and e.get("activity") == "exercise"
    )
    drop = next(e for e in hypoxia["events"] if e.get("crew_id") == "crew-3" and "spo2" in e["targets"])
    spo2 = baselines["crew-3"]["spo2"]

    t_caution = ramp_cross_time(drop, spo2, "spo2", -2)
    t_z3 = ramp_cross_time(drop, spo2, "spo2", -3)
    caution = find_alert(hypoxia, "crew-3", "spo2", "caution")
    warning = find_alert(hypoxia, "crew-3", "spo2", "warning")
    assert_in_window(t_caution, caution)
    assert t_z3 < warning["t_earliest"], "uyarı anında SpO2 zaten 3 SD altında olmalı"

    # Astronot 3, hipoksi kararlarının ikisi sırasında da egzersizde
    for a in (caution, warning):
        assert exercise["t_min"] <= a["t_earliest"] and a["t_latest"] <= exercise["end_min"], a["rule_id"]
    # Kişi uyarısı, kabin alt sınırı aşılmadan önce gelir (erken uyarı)
    assert caution["t_latest"] < find_alert(hypoxia, None, "pp_o2", "warning")["t_earliest"]


def test_hypoxia_exercise_heart_rate_is_not_evidence(hypoxia: dict[str, Any]) -> None:
    forbidden = {(f["crew_id"], f["metric"]) for f in hypoxia["forbidden_alerts"]}
    assert ("crew-3", "heart_rate") in forbidden
    assert ("crew-3", "resp_rate") in forbidden
    for a in hypoxia["expected_alerts"]:
        if a["crew_id"] == "crew-3":
            assert not {"heart_rate", "resp_rate"} & set(a["evidence_metrics"]), a["rule_id"]
            assert "pp_o2" in a["evidence_metrics"], "hipoksi kararı ortamla birleştirilir"
    guarded = {f["rule_id"] for f in hypoxia["forbidden_evidence"] if f["crew_id"] == "crew-3"}
    assert guarded == {"crew_hypoxia_caution", "crew_hypoxia_warning"}


def test_hypoxia_personal_heart_rate_deviation(
    hypoxia: dict[str, Any], baselines: dict[str, dict[str, dict[str, float]]]
) -> None:
    drift = next(e for e in hypoxia["events"] if e.get("crew_id") == "crew-2")
    assert drift["activity"] == "resting"
    hr2, hr4 = baselines["crew-2"]["heart_rate"], baselines["crew-4"]["heart_rate"]

    assert_in_window(
        ramp_cross_time(drift, hr2, "heart_rate", 2), find_alert(hypoxia, "crew-2", "heart_rate", "caution")
    )
    assert_in_window(
        ramp_cross_time(drift, hr2, "heart_rate", 3), find_alert(hypoxia, "crew-2", "heart_rate", "warning")
    )

    # Aynı nabız: crew-2 için alarm, crew-4 için normal (sabit eşik ikisini aynı boyardı)
    final_hr = drift["targets"]["heart_rate"]
    assert (final_hr - hr2["mean"]) / hr2["sd"] >= 3
    assert abs(final_hr - hr4["mean"]) / hr4["sd"] < 2
    assert ("crew-4", "heart_rate") in {(f["crew_id"], f["metric"]) for f in hypoxia["forbidden_alerts"]}
    assert ("crew-2", "spo2") in {(f["crew_id"], f["metric"]) for f in hypoxia["forbidden_alerts"]}
    assert hypoxia["expected_status_end"]["crew-2"] == "warning"
    assert hypoxia["expected_status_end"]["crew-4"] == "nominal"


# ---------------------------------------------------------------- tüm senaryolar: ortak


def by_rule(s: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(a for a in s["expected_alerts"] if a["rule_id"] == rule_id)


def test_required_scenarios_exist() -> None:
    present = {p.stem for p in SCENARIO_FILES}
    assert present >= REQUIRED_SCENARIOS, f"eksik: {REQUIRED_SCENARIOS - present}"


def test_crew_and_cabin_start_are_identical_across_scenarios() -> None:
    reference = load("hypoxia")
    for path in SCENARIO_FILES:
        s = json.loads(path.read_text(encoding="utf-8"))
        assert s["crew"] == reference["crew"], path.stem
        assert s["cabin_initial"] == reference["cabin_initial"], path.stem


# ---------------------------------------------------------------- nominal


def test_nominal_has_no_alerts_even_with_exercise() -> None:
    s = load("nominal")
    assert s["expected_alerts"] == []
    assert s["no_alerts_at_all"] is True
    assert set(s["expected_status_end"].values()) == {"nominal"}
    exercise = next(e for e in s["events"] if e.get("activity") == "exercise")
    forbidden = {(f["crew_id"], f["metric"]) for f in s["forbidden_alerts"]}
    assert {(exercise["crew_id"], "heart_rate"), (exercise["crew_id"], "resp_rate")} <= forbidden


# ---------------------------------------------------------------- hypercapnia


def co2_rise_mmhg_per_hour(s: dict[str, Any]) -> float:
    v = s["assumptions"]
    fail = next(e for e in s["events"] if e["type"] == "co2_removal_rate")
    kg_per_hour = v["crew_count"] * v["co2_production_kg_per_person_day"] / 24
    pa_per_kg = (1 / CO2_MOLAR_KG) * GAS_CONSTANT * v["temperature_K"] / v["free_volume_m3"]
    return kg_per_hour * pa_per_kg / 1000 * MMHG_PER_KPA * (1 - fail["value"])


def test_hypercapnia_timings() -> None:
    s = load("hypercapnia")
    rules = s["proposed_rules"]
    rate = co2_rise_mmhg_per_hour(s)
    fail_t = next(e for e in s["events"] if e["type"] == "co2_removal_rate")["t_min"]
    start = s["cabin_initial"]["pp_co2"]["value"]

    def t_at(level: float) -> float:
        return fail_t + (level - start) / rate * 60

    limit = rules["cabin_pp_co2_warning"]
    t_limit_avg = t_at(limit["threshold"]) + limit["average_window_min"] / 2  # doğrusal artış
    assert_in_window(t_at(rules["cabin_pp_co2_caution"]["threshold"]), by_rule(s, "cabin_pp_co2_caution"))
    assert_in_window(t_limit_avg, by_rule(s, "cabin_pp_co2_warning"))

    countdown = s["expected_countdown"]
    computed = t_at(limit["threshold"]) - (fail_t + 1)
    assert computed == pytest.approx(countdown["co2_min_at_t11"], rel=countdown["tolerance_pct"] / 100)

    symptom = next(e for e in s["events"] if e.get("crew_id") == "crew-3")
    assert symptom["activity"] == "resting"
    assert (
        start + rate * (symptom["t_min"] - fail_t) / 60 >= 2.8
    ), "belirti NASA baş ağrısı aralığında başlamalı"

    base = {c["id"]: c["baseline"] for c in s["crew"]}["crew-3"]
    # Kişi koşulu yalnızca solunum (ranges.json olasi_co2_etkisi); kabin 2,5 mmHg'yi daha önce geçmiş olmalı
    t_rr = ramp_cross_time(symptom, base["resp_rate"], "resp_rate", 2)
    assert t_at(rules["cabin_pp_co2_caution"]["threshold"]) < t_rr
    assert_in_window(t_rr, by_rule(s, "crew_co2_effect_caution"))
    assert by_rule(s, "crew_co2_effect_warning")["t_latest"] >= t_limit_avg


def test_hypercapnia_person_decision_only_for_symptomatic_crew() -> None:
    s = load("hypercapnia")
    crew_alerts = {a["crew_id"] for a in s["expected_alerts"] if a["crew_id"]}
    assert crew_alerts == {"crew-3"}
    for a in s["expected_alerts"]:
        if a["crew_id"]:
            assert "pp_co2" in a["evidence_metrics"], "kişi kararı ortamla birleştirilir"


# ---------------------------------------------------------------- solar_storm


def test_solar_storm_timings_and_totals() -> None:
    s = load("solar_storm")
    v = s["assumptions"]
    ev = {e["type"]: e["t_min"] for e in s["events"]}
    rate = v["event_effective_dose_mSv_per_h"] / 60
    rules = s["proposed_rules"]

    assert ev["sep_end"] - ev["sep_onset"] == v["event_duration_min"]
    assert v["event_absorbed_dose_rate_uGy_per_h"] == pytest.approx(
        v["event_effective_dose_mSv_per_h"] / v["quality_factor"] * 1000, rel=0.001
    )
    assert_in_window(ev["sep_onset"], by_rule(s, "cabin_dose_rate_spe_caution"))
    for rid in ("event_dose_caution", "event_dose_warning"):
        t = ev["sep_onset"] + rules[rid]["cumulative_event_dose_mSv"] / rate
        assert_in_window(t, by_rule(s, rid))
    t_warning = ev["sep_onset"] + rules["event_dose_warning"]["cumulative_event_dose_mSv"] / rate
    assert ev["shelter"] >= t_warning, "sığınak uyarıdan sonra tetiklenir"

    total = rate * (ev["shelter"] - ev["sep_onset"]) + rate * v["shelter_factor"] * (
        ev["sep_end"] - ev["shelter"]
    )
    assert total == pytest.approx(s["expected_dose"]["event_total_mSv"], abs=0.2)
    assert total < v["design_spe_mSv"]

    background = v["gcr_mSv_per_day"] * (v["mission_day"] + s["duration_min"] / 1440)
    worst = max(v["prior_career_dose_mSv"].values()) + background + total
    assert worst / v["career_limit_mSv"] == pytest.approx(s["expected_dose"]["max_career_fraction"], abs=0.01)
    assert worst < 0.9 * v["career_limit_mSv"]


def test_solar_storm_matches_saved_donki_event() -> None:
    s = load("solar_storm")
    saved_path = SCENARIO_DIR.parent / "donki" / "sep_event.json"
    if not saved_path.exists():
        pytest.skip("data/donki/sep_event.json bu dalda yok (feat/qa/donki-sep-event birleşince gelir)")
    selected = json.loads(saved_path.read_text(encoding="utf-8"))["selected"]
    assert s["donki"]["sepID"] == selected["sepID"]
    assert s["donki"]["event_time"] == selected["eventTime"]
    assert s["donki"]["linked_flare"] in [e["activityID"] for e in selected["linkedEvents"]]
