"""Gerçek referansla atmosfer dengesi, sınırlar ve eksik veri davranışı."""

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from backend.atmosphere import (
    CO2_MOLAR_MASS,
    HOURS_PER_DAY,
    O2_MOLAR_MASS,
    SECONDS_PER_DAY,
    CabinState,
    Profile,
    load_profile,
    load_ranges,
    metabolic_rates,
    pressure_change_mmhg,
    step,
)


@pytest.fixture
def reference_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "reference" / "ranges.json"


@pytest.fixture
def reference(reference_path: Path) -> dict[str, Any]:
    return json.loads(reference_path.read_text(encoding="utf-8"))


@pytest.fixture
def profile(reference_path: Path) -> Profile:
    return load_profile(reference_path, name="mars_transit")


@pytest.fixture
def crew(reference: dict[str, Any]) -> list[tuple[float, float]]:
    """HİPOTEZ: Referanstaki demo baz nabızları dinlenme girdisidir."""
    people = reference["vital_karar_parametreleri"]["baz_cizgi"]["astronotlar"]
    return [(person["nabiz"], person["nabiz"]) for person in people]


def pp_from_pio2(pio2: float, profile: Profile) -> float:
    """Test girdisindeki solunan basıncı kabin kısmi basıncına çevirir."""
    return (
        pio2 * profile.total_pressure_mmhg / (profile.total_pressure_mmhg - profile.ranges.water_vapour_mmhg)
    )


@pytest.fixture
def state(profile: Profile) -> CabinState:
    return CabinState(pp_from_pio2(149.0, profile), 1.0)


@pytest.fixture
def balanced(profile: Profile) -> Profile:
    """HİPOTEZ: Denge sınaması için kapasiteleri dinlenme yüküne eşitler."""
    metabolism = profile.ranges.metabolism
    return replace(
        profile,
        o2_supply_kg_per_day=profile.crew_count * metabolism.o2.minimum,
        co2_removal_kg_per_day=profile.crew_count * metabolism.co2.minimum,
    )


def write_reference(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def change(data: dict[str, Any], keys: tuple[str | int, ...], value: object) -> None:
    """Geçersiz yapı sınamaları için yalnızca fixture kopyasını değiştirir."""
    parent = data
    for key in keys[:-1]:
        parent = parent[key]
    parent[keys[-1]] = value


def test_real_reference(profile: Profile, reference: dict[str, Any], reference_path: Path) -> None:
    row = reference["profiller"]["mars_transit"]
    assert profile.volume_m3 == row["hacim_m3"]
    assert profile.crew_count == row["kisi"] == 4
    assert profile.total_pressure_mmhg == row["toplam_basinc_mmHg"]
    assert profile.o2_supply_kg_per_day == row["o2_uretim_kg_gun"]
    assert profile.co2_removal_kg_per_day == row["co2_giderme_kg_gun"]
    assert profile.pio2_min_mmhg == row["o2_limit"]["pio2_min"]
    assert profile.co2_max_mmhg == row["co2_limit"]["deger"]
    assert profile.temperature_k == 295.0
    assert profile.ranges == load_ranges(reference_path)
    assert "HİPOTEZ" in profile.limit_source


def test_nominal_balance(balanced: Profile, state: CabinState, crew: list[tuple[float, float]]) -> None:
    result = step(state, balanced, crew, 60.0)
    assert result.state == state
    assert result.status == "nominal"
    assert result.hours_until_co2_limit is None
    assert result.hours_until_o2_limit is None
    assert result.pio2_mmhg == pytest.approx(149.0)
    assert result.estimated is True
    assert "HİPOTEZ" in result.consumption_source
    assert "NASA-STD-3001 metabolik yük tabloları" in result.consumption_source


def test_sufficient_removal_stays_flat_at_zero(profile: Profile, crew: list[tuple[float, float]]) -> None:
    initial = CabinState(pp_from_pio2(149.0, profile), 0.0)
    result = step(initial, profile, crew, 60.0)
    assert result.state.pp_co2_mmhg == 0.0
    assert result.hours_until_co2_limit is None
    assert result.status == "nominal"


def test_no_removal_countdown_decreases(
    profile: Profile, state: CabinState, crew: list[tuple[float, float]]
) -> None:
    profile = replace(profile, co2_removal_kg_per_day=0.0)
    first = step(state, profile, crew, 60.0)
    second = step(first.state, profile, crew, 60.0)
    assert state.pp_co2_mmhg < first.state.pp_co2_mmhg < second.state.pp_co2_mmhg
    assert 0 < second.hours_until_co2_limit < first.hours_until_co2_limit
    assert first.hours_until_co2_limit - second.hours_until_co2_limit == pytest.approx(1 / 60)


def test_supply_failure_affects_only_o2(
    profile: Profile, state: CabinState, crew: list[tuple[float, float]]
) -> None:
    profile = replace(profile, co2_removal_kg_per_day=0.0)
    working = step(state, profile, crew, 60.0)
    failed = step(state, profile, crew, 60.0, o2_supply_failed=True)
    assert working.hours_until_o2_limit is None
    assert failed.hours_until_o2_limit > 0
    assert failed.state.pp_o2_mmhg < working.state.pp_o2_mmhg
    assert failed.state.pp_co2_mmhg == working.state.pp_co2_mmhg
    assert failed.hours_until_co2_limit == working.hours_until_co2_limit
    assert failed.hours_until_co2_limit > 0
    assert failed == step(state, replace(profile, o2_supply_kg_per_day=0.0), crew, 60.0)


def test_exercise_shortens_o2_countdown(
    profile: Profile, state: CabinState, crew: list[tuple[float, float]]
) -> None:
    exercise = [(baseline * 1.8, baseline) for _, baseline in crew]
    resting = step(state, profile, crew, 60.0, o2_supply_failed=True)
    exercising = step(state, profile, exercise, 60.0, o2_supply_failed=True)
    assert exercising.state.pp_o2_mmhg < resting.state.pp_o2_mmhg
    assert exercising.state.pp_co2_mmhg > resting.state.pp_co2_mmhg
    assert 0 < exercising.hours_until_o2_limit < resting.hours_until_o2_limit


@pytest.mark.parametrize(
    ("ratio", "o2", "co2"),
    [(0.5, 0.49, 0.52), (1.0, 0.49, 0.52), (1.4, 0.87, 1.01), (1.8, 1.25, 1.5), (2.0, 1.25, 1.5)],
)
def test_personal_scaling(profile: Profile, ratio: float, o2: float, co2: float) -> None:
    assert metabolic_rates(70.0 * ratio, 70.0, profile.ranges.metabolism) == pytest.approx((o2, co2))
    assert metabolic_rates(50.0 * ratio, 50.0, profile.ranges.metabolism) == pytest.approx((o2, co2))


# Referansta CO2 giderimi (4,0 kg/gün) üretimden büyüktür; net CO2 akışı limitten
# uzağa doğru olduğu için limit altında geri sayım yoktur (None). Limit ve üstünde
# geri sayım sıfırdır.
@pytest.mark.parametrize(
    ("co2", "expected", "hours"),
    [
        (2.5, "nominal", None),
        (math.nextafter(2.5, math.inf), "caution", None),
        (3.0, "caution", 0.0),
        (math.nextafter(3.0, math.inf), "critical", 0.0),
    ],
)
def test_co2_strict_boundaries(
    profile: Profile,
    state: CabinState,
    crew: list[tuple[float, float]],
    co2: float,
    expected: str,
    hours: float | None,
) -> None:
    assert profile.ranges.rule("ppco2-limit").comparator == ">"
    assert profile.ranges.rule("ppco2-limit").threshold == 3.0
    assert profile.ranges.rule("ppco2-caution").comparator == ">"
    assert profile.ranges.rule("ppco2-caution").threshold == 2.5
    result = step(replace(state, pp_co2_mmhg=co2), profile, crew, 0.0)
    assert result.status == expected
    assert result.hours_until_co2_limit == hours


@pytest.mark.parametrize(
    ("pio2", "expected"),
    [
        (127.0, "caution"),
        (127.0 - 1e-6, "critical"),
        (145.0, "nominal"),
        (145.0 - 1e-6, "caution"),
        (155.0, "nominal"),
        (155.0 + 1e-6, "caution"),
    ],
)
def test_pio2_strict_boundaries(
    profile: Profile, crew: list[tuple[float, float]], pio2: float, expected: str
) -> None:
    assert profile.ranges.rule("pio2-hypoxia-low").comparator == "<"
    assert profile.ranges.rule("pio2-hypoxia-low").threshold == 127.0
    assert profile.ranges.rule("pio2-normoxia-low").threshold == 145.0
    assert profile.ranges.rule("pio2-normoxia-high").threshold == 155.0
    result = step(CabinState(pp_from_pio2(pio2, profile), 1.0), profile, crew, 0.0)
    assert result.status == expected
    assert result.pio2_mmhg == pytest.approx(pio2)
    if pio2 <= profile.pio2_min_mmhg:
        assert result.hours_until_o2_limit == 0.0
    else:
        assert result.hours_until_o2_limit is None or result.hours_until_o2_limit > 0.0


def test_one_kg_co2_conversion(profile: Profile) -> None:
    profile = replace(profile, volume_m3=100.0)
    assert profile.temperature_k == 295.0
    assert pressure_change_mmhg(1.0, CO2_MOLAR_MASS, profile) == pytest.approx(4.18, abs=0.02)
    assert pressure_change_mmhg(-1.0, CO2_MOLAR_MASS, profile) == pytest.approx(-4.18, abs=0.02)


def test_mars_transit_reference_countdown(
    profile: Profile, state: CabinState, crew: list[tuple[float, float]], reference: dict[str, Any]
) -> None:
    metabolism = profile.ranges.metabolism
    ratio = metabolism.r_min + (metabolism.r_max - metabolism.r_min) * (
        (metabolism.o2.average - metabolism.o2.minimum) / (metabolism.o2.maximum - metabolism.o2.minimum)
    )
    average_crew = [(ratio * baseline, baseline) for _, baseline in crew]
    assert metabolic_rates(*average_crew[0], metabolism)[0] == pytest.approx(0.84)
    result = step(state, profile, average_crew, 0.0, o2_supply_failed=True)
    assert "22 saat" in reference["kalan_sure_formulu"]["ornek_hesap"]
    assert result.hours_until_o2_limit == pytest.approx(22.0, rel=0.05)
    normoxia_limit = pp_from_pio2(profile.ranges.rule("pio2-normoxia-low").threshold, profile)
    daily_loss = pressure_change_mmhg(profile.crew_count * metabolism.o2.average, O2_MOLAR_MASS, profile)
    normoxia_hours = (state.pp_o2_mmhg - normoxia_limit) / daily_loss * HOURS_PER_DAY
    assert normoxia_hours == pytest.approx(4.0, abs=0.05)


@pytest.mark.parametrize("during_step", [False, True])
@pytest.mark.parametrize("limit_exceeded", [False, True])
def test_total_pressure_is_scaled_and_status_keeps_priority(
    profile: Profile, crew: list[tuple[float, float]], during_step: bool, limit_exceeded: bool
) -> None:
    profile = replace(profile, co2_removal_kg_per_day=0.0, o2_supply_kg_per_day=0.0)
    if limit_exceeded:
        initial = CabinState(600.0, 160.0 if during_step else 200.0)
    else:
        initial = CabinState(profile.total_pressure_mmhg if during_step else 800.0, 0.0)
    dt = 60.0 if during_step else 0.0
    if during_step:
        profile = replace(profile, o2_supply_kg_per_day=100.0)
    result = step(initial, profile, crew, dt)
    assert result.status == ("critical" if limit_exceeded else "unknown")
    assert result.state.pp_o2_mmhg + result.state.pp_co2_mmhg == pytest.approx(profile.total_pressure_mmhg)
    if not during_step:
        scale = profile.total_pressure_mmhg / (initial.pp_o2_mmhg + initial.pp_co2_mmhg)
        assert result.state.pp_o2_mmhg == pytest.approx(initial.pp_o2_mmhg * scale)
        assert result.state.pp_co2_mmhg == pytest.approx(initial.pp_co2_mmhg * scale)


def test_inconsistent_pressure_with_missing_data_returns_unknown(profile: Profile) -> None:
    result = step(CabinState(profile.total_pressure_mmhg + 1, None), profile, [(None, None)] * 4, 0.0)
    assert result.status == "unknown"
    assert result.state.pp_o2_mmhg == profile.total_pressure_mmhg
    assert result.state.pp_co2_mmhg is None


@pytest.mark.parametrize("invalid", [None, -1.0, 0.0, float("nan"), float("inf")])
@pytest.mark.parametrize("index", [0, 1])
def test_invalid_personal_measurement_uses_average_and_unknown(
    profile: Profile, state: CabinState, crew: list[tuple[float, float]], invalid: float | None, index: int
) -> None:
    pair = list(crew[0])
    pair[index] = invalid
    rates = [tuple(pair), *crew[1:]]
    result = step(state, profile, rates, 60.0)
    assert result.status == "unknown"
    metabolism = profile.ranges.metabolism
    assert metabolic_rates(*pair, metabolism) == (metabolism.o2.average, metabolism.co2.average)
    o2_mass = profile.o2_supply_kg_per_day - metabolism.o2.average - 3 * metabolism.o2.minimum
    co2_mass = metabolism.co2.average + 3 * metabolism.co2.minimum - profile.co2_removal_kg_per_day
    assert result.state.pp_o2_mmhg == pytest.approx(
        state.pp_o2_mmhg + pressure_change_mmhg(o2_mass, O2_MOLAR_MASS, profile) * 60.0 / SECONDS_PER_DAY
    )
    assert result.state.pp_co2_mmhg == pytest.approx(
        state.pp_co2_mmhg + pressure_change_mmhg(co2_mass, CO2_MOLAR_MASS, profile) * 60.0 / SECONDS_PER_DAY
    )


@pytest.mark.parametrize("gas", ["pp_o2_mmhg", "pp_co2_mmhg"])
@pytest.mark.parametrize("invalid", [None, -1.0, float("nan"), float("inf")])
def test_invalid_pressure_unknown(
    profile: Profile, state: CabinState, crew: list[tuple[float, float]], gas: str, invalid: float | None
) -> None:
    result = step(replace(state, **{gas: invalid}), profile, crew, 60.0)
    assert result.status == "unknown"
    assert getattr(result.state, gas) is None
    countdown = "hours_until_o2_limit" if gas == "pp_o2_mmhg" else "hours_until_co2_limit"
    assert getattr(result, countdown) is None


@pytest.mark.parametrize("gas", ["o2", "co2"])
@pytest.mark.parametrize("missing", ["heart", "baseline", "other_pressure"])
def test_missing_data_does_not_hide_critical(
    profile: Profile, crew: list[tuple[float, float]], gas: str, missing: str
) -> None:
    initial = CabinState(
        pp_from_pio2(126.0 if gas == "o2" else 149.0, profile), 3.01 if gas == "co2" else 1.0
    )
    rates = crew
    if missing == "heart":
        rates = [(None, crew[0][1]), *crew[1:]]
    elif missing == "baseline":
        rates = [(crew[0][0], None), *crew[1:]]
    else:
        initial = replace(initial, **{"pp_co2_mmhg" if gas == "o2" else "pp_o2_mmhg": None})
    result = step(initial, profile, rates, 0.0)
    assert result.status == "critical"
    assert getattr(result, f"hours_until_{gas}_limit") == 0.0


def test_unknown_has_priority_over_caution(profile: Profile, state: CabinState) -> None:
    result = step(replace(state, pp_co2_mmhg=2.9), profile, [(None, None)] * 4, 0.0)
    assert result.status == "unknown"


def test_flows_away_from_limits(profile: Profile, state: CabinState, crew: list[tuple[float, float]]) -> None:
    result = step(state, replace(profile, o2_supply_kg_per_day=10.0, co2_removal_kg_per_day=10.0), crew, 60.0)
    assert result.hours_until_o2_limit is None
    assert result.hours_until_co2_limit is None
    assert result.state.pp_o2_mmhg > state.pp_o2_mmhg
    assert result.state.pp_co2_mmhg < state.pp_co2_mmhg


@pytest.mark.parametrize(
    ("rule_id", "expected"),
    [
        ("ppco2-limit", "critical"),
        ("pio2-hypoxia-low", "critical"),
        ("ppco2-caution", "caution"),
        ("pio2-normoxia-low", "caution"),
        ("pio2-normoxia-high", "caution"),
    ],
)
def test_comparators_come_from_reference(
    reference: dict[str, Any], tmp_path: Path, crew: list[tuple[float, float]], rule_id: str, expected: str
) -> None:
    row = next(row for row in reference["kurallar"] if row["id"] == rule_id)
    row["comparator"] = "<" if row["comparator"] == ">" else ">"
    profile = load_profile(write_reference(tmp_path, reference), name="mars_transit")
    assert step(CabinState(pp_from_pio2(149.0, profile), 1.0), profile, crew, 0.0).status == expected


def test_reference_parameters_are_not_hardcoded(
    reference: dict[str, Any], tmp_path: Path, crew: list[tuple[float, float]]
) -> None:
    reference["metabolik"]["o2_tuketimi"].update(min=0.2, ortalama=0.4, max=0.6)
    reference["metabolik"]["co2_uretimi"].update(min=0.3, ortalama=0.5, max=0.7)
    reference["metabolik"]["nabiz_o2_olcekleme"].update(r_min=0.5, r_max=1.5)
    reference["kalan_sure_formulu"]["not"] = reference["kalan_sure_formulu"]["not"].replace("295 K", "300 K")
    reference["kabin"]["pio2_formulu"]["formul"] = "PIO2 = (PB - 40) × FIO2"
    reference["kurallar"].reverse()
    for row in reference["kurallar"]:
        row.pop("status")
        row["consecutive_required"] = "bu modülde kullanılmaz"
    row = next(row for row in reference["kurallar"] if row["id"] == "ppco2-limit")
    row["threshold"] = 0.9
    profile = load_profile(write_reference(tmp_path, reference), name="mars_transit")
    assert profile.temperature_k == 300.0
    assert profile.ranges.water_vapour_mmhg == 40.0
    assert metabolic_rates(70.0, 70.0, profile.ranges.metabolism) == pytest.approx((0.4, 0.5))
    assert step(CabinState(pp_from_pio2(149.0, profile), 1.0), profile, crew, 0.0).status == "critical"


@pytest.mark.parametrize(
    ("keys", "value", "message"),
    [
        (("kurallar",), {}, "kurallar"),
        (("kurallar",), [], "eksik kimlikler"),
        (("kurallar", 0), None, "nesne"),
        (("kurallar", 0, "id"), None, "id"),
        (("kurallar", 0, "comparator"), ">=", "comparator"),
        (("kurallar", 0, "threshold"), -1, "threshold"),
        (("kurallar", 0, "threshold"), "145", "threshold"),
        (("kurallar", 0, "threshold"), float("nan"), "threshold"),
        (("kurallar", 0, "metric"), "pp_o2", "metric"),
        (("kurallar", 0, "severity"), "warning", "severity"),
        (("metabolik",), [], "metabolik"),
        (("metabolik", "o2_tuketimi", "ortalama"), None, "ortalama"),
        (("metabolik", "o2_tuketimi", "min"), 2, "min ≤ ortalama"),
        (("metabolik", "co2_uretimi", "max"), True, "max"),
        (("metabolik", "nabiz_o2_olcekleme", "r_min"), 1.8, "r_min < r_max"),
        (("metabolik", "nabiz_o2_olcekleme", "r_max"), float("inf"), "r_max"),
        (("kalan_sure_formulu", "not"), "Sıcaklık belirtilmemiş.", "not.T"),
        (("kalan_sure_formulu", "not"), "T = 295 K; T = 300 K", "not.T"),
        (("kabin", "pio2_formulu", "formul"), "PIO2 belirsiz", "su_buharı"),
    ],
)
def test_invalid_ranges(
    reference: dict[str, Any], tmp_path: Path, keys: tuple[str | int, ...], value: object, message: str
) -> None:
    change(reference, keys, value)
    path = write_reference(tmp_path, reference)
    for loader in (load_ranges, lambda path: load_profile(path, name="mars_transit")):
        with pytest.raises(ValueError, match=message):
            loader(path)


@pytest.mark.parametrize(
    ("keys", "value", "message"),
    [
        (("hacim_m3",), 0, "hacim_m3"),
        (("kisi",), -1, "crew_count"),
        (("kisi",), 4.5, "crew_count"),
        (("kisi",), True, "crew_count"),
        (("toplam_basinc_mmHg",), 47, "su buharı"),
        (("co2_giderme_kg_gun",), -1, "co2_giderme"),
        (("o2_uretim_kg_gun",), None, "o2_uretim"),
        (("o2_limit", "pio2_min"), 800, "toplam basıncı"),
        (("co2_limit", "deger"), float("inf"), "deger"),
        (("co2_limit",), [], "co2_limit"),
    ],
)
def test_invalid_profile(
    reference: dict[str, Any], tmp_path: Path, keys: tuple[str | int, ...], value: object, message: str
) -> None:
    change(reference, ("profiller", "mars_transit", *keys), value)
    with pytest.raises(ValueError, match=message):
        load_profile(write_reference(tmp_path, reference), name="mars_transit")


def test_missing_fields_and_duplicate_rules(reference: dict[str, Any], tmp_path: Path) -> None:
    reference["kurallar"].append(reference["kurallar"][0].copy())
    with pytest.raises(ValueError, match="yinelenen"):
        load_ranges(write_reference(tmp_path, reference))
    reference["kurallar"].pop()
    del reference["metabolik"]["o2_tuketimi"]["min"]
    with pytest.raises(ValueError, match="min: eksik"):
        load_ranges(write_reference(tmp_path, reference))


def test_missing_profile_fields(reference: dict[str, Any], tmp_path: Path) -> None:
    del reference["profiller"]["mars_transit"]["hacim_m3"]
    with pytest.raises(ValueError, match="hacim_m3: eksik"):
        load_profile(write_reference(tmp_path, reference), name="mars_transit")


def test_unknown_profile_and_wrong_root(reference_path: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="profiller.bilinmeyen"):
        load_profile(reference_path, name="bilinmeyen")
    with pytest.raises(ValueError, match="ranges: nesne"):
        load_ranges(write_reference(tmp_path, []))
    path = tmp_path / "bozuk.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="geçersiz JSON"):
        load_ranges(path)
    with pytest.raises(FileNotFoundError):
        load_ranges(tmp_path / "eksik.json")


@pytest.mark.parametrize("dt", [-1.0, float("nan"), float("inf")])
def test_invalid_time(
    profile: Profile, state: CabinState, crew: list[tuple[float, float]], dt: float
) -> None:
    with pytest.raises(ValueError, match="dt_seconds"):
        step(state, profile, crew, dt)


def test_invalid_crew_and_runtime_profile(
    profile: Profile, state: CabinState, crew: list[tuple[float, float]]
) -> None:
    with pytest.raises(ValueError, match="her kişi"):
        step(state, profile, [], 60.0)
    with pytest.raises(ValueError, match="çifti"):
        step(state, profile, [None] * 4, 60.0)
    with pytest.raises(ValueError, match="volume_m3"):
        step(state, replace(profile, volume_m3=0.0), crew, 60.0)
