"""Pure atmosphere balance and inclusive alarm-boundary tests."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from backend.atmosphere import (
    CO2_MOLAR_MASS,
    CabinState,
    Profile,
    load_profile,
    load_ranges,
    metabolic_rates,
    pressure_change_mmhg,
    step,
)
from backend.models import Status

CREW_COUNT = 4
REST_HR = 60.0
EXERCISE_HR = 150.0
STEP_SECONDS = 60.0
SECONDS_PER_HOUR = 3600.0
CO2_LIMIT = 3.0  # HİPOTEZ: instantaneous >= rule; NASA [V2 6004] uses a one-hour average.
REST_RATES = [REST_HR] * CREW_COUNT


@pytest.fixture
def profile() -> Profile:
    o2_rate, co2_rate = metabolic_rates(REST_HR)
    return Profile(
        volume_m3=100.0,
        crew_count=CREW_COUNT,
        total_pressure_mmhg=760.0,
        o2_supply_kg_per_day=CREW_COUNT * o2_rate,
        co2_removal_kg_per_day=CREW_COUNT * co2_rate,
        o2_min_mmhg=120.0,
        co2_max_mmhg=CO2_LIMIT,
        limit_source="CO2: NASA-STD-3001 [V2 6004]; instantaneous >= and O2: HİPOTEZ / varsayım",
    )


@pytest.fixture
def state() -> CabinState:
    return CabinState(pp_o2_mmhg=160.0, pp_co2_mmhg=1.0)


def test_nominal_balance(profile: Profile, state: CabinState) -> None:
    result = step(state, profile, REST_RATES, STEP_SECONDS)
    assert result.state == state
    assert result.hours_until_co2_limit is None
    assert result.hours_until_o2_limit is None
    assert result.status == Status.nominal.value
    assert result.estimated is True
    assert "NASA-STD-3001 metabolik yuk tablolari" in result.consumption_source
    assert "HİPOTEZ / varsayım: 60-150 bpm" in result.consumption_source


def test_no_removal_countdown_decreases(profile: Profile, state: CabinState) -> None:
    profile = replace(profile, co2_removal_kg_per_day=0.0)
    first = step(state, profile, REST_RATES, STEP_SECONDS)
    second = step(first.state, profile, REST_RATES, STEP_SECONDS)
    assert first.state.pp_co2_mmhg > state.pp_co2_mmhg
    assert second.state.pp_co2_mmhg > first.state.pp_co2_mmhg
    assert 0 < second.hours_until_co2_limit < first.hours_until_co2_limit
    assert first.hours_until_co2_limit - second.hours_until_co2_limit == pytest.approx(
        STEP_SECONDS / SECONDS_PER_HOUR
    )
    daily_pressure = pressure_change_mmhg(CREW_COUNT * 0.52, CO2_MOLAR_MASS, profile)
    assert first.hours_until_co2_limit == pytest.approx(
        (CO2_LIMIT - first.state.pp_co2_mmhg) / daily_pressure * 24.0
    )


def test_supply_failure_affects_only_o2(profile: Profile, state: CabinState) -> None:
    profile = replace(profile, co2_removal_kg_per_day=0.0)
    working = step(state, profile, REST_RATES, STEP_SECONDS)
    failed = step(state, profile, REST_RATES, STEP_SECONDS, o2_supply_failed=True)
    assert working.hours_until_o2_limit is None
    assert failed.hours_until_o2_limit > 0
    assert failed.state.pp_o2_mmhg < working.state.pp_o2_mmhg
    assert failed.state.pp_co2_mmhg == working.state.pp_co2_mmhg
    assert failed.hours_until_co2_limit == working.hours_until_co2_limit


def test_exercise_shortens_o2_countdown(profile: Profile, state: CabinState) -> None:
    resting = step(state, profile, REST_RATES, STEP_SECONDS, o2_supply_failed=True)
    exercising = step(state, profile, [EXERCISE_HR] * CREW_COUNT, STEP_SECONDS, o2_supply_failed=True)
    assert metabolic_rates(EXERCISE_HR)[0] > metabolic_rates(REST_HR)[0]
    assert exercising.state.pp_o2_mmhg < resting.state.pp_o2_mmhg
    assert 0 < exercising.hours_until_o2_limit < resting.hours_until_o2_limit


@pytest.mark.parametrize("co2", [3.0, 3.1])
def test_co2_inclusive_limit(profile: Profile, co2: float) -> None:
    # Exactly 3.0 mmHg must alarm: comparison is >=, not >.
    result = step(CabinState(160.0, co2), profile, REST_RATES, 0.0)
    assert profile.co2_max_mmhg == 3.0
    assert result.status == "warning"
    assert result.hours_until_co2_limit == 0.0


def test_one_kg_co2_conversion(profile: Profile) -> None:
    assert profile.volume_m3 == 100.0
    assert profile.temperature_k == 295.15
    assert pressure_change_mmhg(1.0, CO2_MOLAR_MASS, profile) == pytest.approx(4.18, abs=0.01)


def test_total_pressure_rejected(profile: Profile) -> None:
    with pytest.raises(ValueError, match="partial pressures exceed"):
        step(CabinState(759.0, 2.0), profile, REST_RATES, 0.0)
    rising = replace(profile, co2_removal_kg_per_day=0.0)
    with pytest.raises(ValueError, match="partial pressures exceed"):
        step(CabinState(758.0, 2.0), rising, REST_RATES, STEP_SECONDS)


def test_missing_hr_unknown(profile: Profile, state: CabinState) -> None:
    result = step(state, profile, [None] * CREW_COUNT, STEP_SECONDS)
    assert metabolic_rates(None) == (0.84, 1.0)
    assert result.status == "unknown"
    assert result.state.pp_o2_mmhg < state.pp_o2_mmhg
    assert result.state.pp_co2_mmhg > state.pp_co2_mmhg


@pytest.mark.parametrize("missing", [CabinState(None, 1.0), CabinState(160.0, None)])
def test_missing_pressure_stays_unknown(profile: Profile, missing: CabinState) -> None:
    result = step(missing, profile, REST_RATES, STEP_SECONDS)
    assert result.status == "unknown"
    if missing.pp_o2_mmhg is None:
        assert result.state.pp_o2_mmhg is None
        assert result.hours_until_o2_limit is None
    else:
        assert result.state.pp_co2_mmhg is None
        assert result.hours_until_co2_limit is None


@pytest.mark.parametrize("gas", ["o2", "co2"])
@pytest.mark.parametrize("missing", ["heart_rate", "other_pressure"])
def test_missing_data_does_not_hide_limit(profile: Profile, gas: str, missing: str) -> None:
    state = CabinState(profile.o2_min_mmhg, 1.0) if gas == "o2" else CabinState(160.0, CO2_LIMIT)
    rates = [None, *REST_RATES[1:]] if missing == "heart_rate" else REST_RATES
    if missing == "other_pressure":
        state = replace(state, **{"pp_co2_mmhg" if gas == "o2" else "pp_o2_mmhg": None})
    result = step(state, profile, rates, 0.0)
    assert result.status == Status.warning.value
    assert getattr(result, f"hours_until_{gas}_limit") == 0.0
    if missing == "other_pressure":
        other_gas = "co2" if gas == "o2" else "o2"
        assert getattr(result, f"hours_until_{other_gas}_limit") is None


def test_o2_limit_and_flows_away(profile: Profile) -> None:
    at_limit = step(CabinState(profile.o2_min_mmhg, 1.0), profile, REST_RATES, 0.0)
    assert at_limit.hours_until_o2_limit == 0.0
    assert at_limit.status == "warning"
    recovering = replace(profile, o2_supply_kg_per_day=10.0, co2_removal_kg_per_day=10.0)
    result = step(CabinState(160.0, 1.0), recovering, REST_RATES, STEP_SECONDS)
    assert result.hours_until_o2_limit is None
    assert result.hours_until_co2_limit is None


def test_hr_clamped() -> None:
    assert metabolic_rates(0.0) == metabolic_rates(REST_HR)
    assert metabolic_rates(200.0) == metabolic_rates(EXERCISE_HR)


def reference_rule(metric: str, comparator: str, threshold: float) -> dict[str, str | float | int]:
    """Synthetic reference row; numeric values are test fixtures, not validated limits."""
    return {
        "id": f"test_{metric}_{comparator}",
        "metric": metric,
        "severity": "warning",
        "comparator": comparator,
        "threshold": threshold,
        "consecutive_required": 1,
        "source": "varsayım",
        "status": "doğrulanmadı",
    }


@pytest.mark.parametrize("lower", ["<", "<="])
@pytest.mark.parametrize("upper", [">", ">="])
def test_load_ranges_from_fixture(tmp_path: Path, lower: str, upper: str) -> None:
    path = tmp_path / "ranges.json"
    expected = {"pio2_min": 139.0, "pio2_max": 168.0}
    rules = [
        reference_rule("pp_o2", "<", 120.0),
        reference_rule("pio2", upper, expected["pio2_max"]),
        reference_rule("pp_co2", ">=", CO2_LIMIT),
        reference_rule("pio2", lower, expected["pio2_min"]),
    ]
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    assert load_ranges(path) == expected
    with pytest.raises(FileNotFoundError):
        load_ranges(tmp_path / "missing.json")


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ({"pio2_min": 139.0, "pio2_max": 168.0}, "list of reference rule rows"),
        ([reference_rule("pp_o2", "<", 120.0)], "lower and upper"),
        ([reference_rule("pio2", "<", 139.0)], "lower and upper"),
        ([reference_rule("pio2", ">", 168.0)], "lower and upper"),
        ([reference_rule("pio2", "<", 168.0), reference_rule("pio2", ">", 139.0)], "cannot exceed"),
        ([reference_rule("pio2", "<", -1.0), reference_rule("pio2", ">", 168.0)], "nonnegative"),
        ([reference_rule("pio2", "<", 139.0), reference_rule("pio2", ">", float("inf"))], "finite"),
        ([reference_rule("pio2", "<", 139.0), reference_rule("pio2", "<=", 139.0)], "ambiguous"),
    ],
)
def test_load_ranges_rejects_invalid_bounds(tmp_path: Path, rules: object, message: str) -> None:
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_ranges(path)


def test_load_example_profile() -> None:
    path = Path(__file__).resolve().parents[1] / "backend" / "profiles.json"
    profile = load_profile(path, "mars_transit")
    assert profile.crew_count == CREW_COUNT
    assert profile.co2_max_mmhg == CO2_LIMIT
    assert "NASA-STD-3001 [V2 6004]" in profile.limit_source
    assert "HİPOTEZ / varsayım: anlık ppCO2 >= 3 mmHg" in profile.limit_source
    assert "ORNEK/HIPOTEZ" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("dt", [-1.0, float("nan"), float("inf")])
def test_invalid_time(profile: Profile, state: CabinState, dt: float) -> None:
    with pytest.raises(ValueError):
        step(state, profile, REST_RATES, dt)


def test_invalid_crew_and_volume(profile: Profile, state: CabinState) -> None:
    with pytest.raises(ValueError, match="one heart rate"):
        step(state, profile, [], STEP_SECONDS)
    with pytest.raises(ValueError, match="volume_m3"):
        step(state, replace(profile, volume_m3=0.0), REST_RATES, STEP_SECONDS)
