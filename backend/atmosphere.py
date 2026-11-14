"""Estimated ideal-gas balance: instantaneous ppCO2 >= 3 mmHg is HİPOTEZ.

NASA-STD-3001 [V2 6004] specifies 3 mmHg CO2 as a one-hour average;
this instantaneous model does not calculate that rolling average. PIO2 ranges
are external reference data, not cabin O2 partial-pressure limits. Countdowns
start at the returned state. Pressures exceeding total pressure raise ValueError.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path

GAS_CONSTANT = 8.314
PASCALS_PER_MMHG = 133.322
GRAMS_PER_KG = 1000.0
O2_MOLAR_MASS = 32.0
CO2_MOLAR_MASS = 44.0
SECONDS_PER_DAY = 86400.0
HOURS_PER_DAY = 24.0
DEFAULT_TEMPERATURE_K = 295.15  # 22 C varsayimi
REST_HEART_RATE = 60.0  # HİPOTEZ / varsayım: lower anchor for linear HR scaling.
EXERCISE_HEART_RATE = 150.0  # HİPOTEZ / varsayım: upper anchor for linear HR scaling.
REST_O2_RATE = 0.49
EXERCISE_O2_RATE = 1.25
REST_CO2_RATE = 0.52
EXERCISE_CO2_RATE = 1.5
DEFAULT_O2_RATE = 0.84
DEFAULT_CO2_RATE = 1.0
CONSUMPTION_SOURCE = (
    "tahmini (NASA-STD-3001 metabolik yuk tablolari); "
    "HİPOTEZ / varsayım: 60-150 bpm dogrusal nabiz olceklemesi"
)


@dataclass(frozen=True)
class Profile:
    volume_m3: float
    crew_count: int
    total_pressure_mmhg: float
    o2_supply_kg_per_day: float
    co2_removal_kg_per_day: float
    o2_min_mmhg: float
    co2_max_mmhg: float
    limit_source: str
    temperature_k: float = DEFAULT_TEMPERATURE_K


@dataclass(frozen=True)
class CabinState:
    pp_o2_mmhg: float | None
    pp_co2_mmhg: float | None


@dataclass(frozen=True)
class Result:
    state: CabinState
    hours_until_o2_limit: float | None
    hours_until_co2_limit: float | None
    status: str
    estimated: bool = True
    consumption_source: str = CONSUMPTION_SOURCE


def _nonnegative(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


def _validate_profile(profile: Profile) -> None:
    for name in ("volume_m3", "total_pressure_mmhg", "temperature_k"):
        value = getattr(profile, name)
        _nonnegative(name, value)
        if value == 0:
            raise ValueError(f"{name} must be positive")
    count = profile.crew_count
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("crew_count must be a nonnegative integer")
    for name in ("o2_supply_kg_per_day", "co2_removal_kg_per_day", "o2_min_mmhg", "co2_max_mmhg"):
        _nonnegative(name, getattr(profile, name))
    if max(profile.o2_min_mmhg, profile.co2_max_mmhg) > profile.total_pressure_mmhg:
        raise ValueError("limits exceed total pressure")


def _validate_state(state: CabinState, total: float) -> None:
    pressures = (state.pp_o2_mmhg, state.pp_co2_mmhg)
    for value in pressures:
        if value is not None:
            _nonnegative("partial pressure", value)
    if sum(value for value in pressures if value is not None) > total:
        raise ValueError("partial pressures exceed total pressure")


def pressure_change_mmhg(mass_kg: float, molar_mass: float, profile: Profile) -> float:
    """Convert signed gas mass to pressure via the ideal gas law."""
    _validate_profile(profile)
    if not math.isfinite(mass_kg) or not math.isfinite(molar_mass) or molar_mass <= 0:
        raise ValueError("mass must be finite and molar mass positive and finite")
    return (
        mass_kg
        * GRAMS_PER_KG
        * GAS_CONSTANT
        * profile.temperature_k
        / (molar_mass * profile.volume_m3 * PASCALS_PER_MMHG)
    )


def metabolic_rates(heart_rate: float | None) -> tuple[float, float]:
    """Return estimated O2 consumption and CO2 production in kg/person/day."""
    if heart_rate is None:
        return DEFAULT_O2_RATE, DEFAULT_CO2_RATE
    _nonnegative("heart_rate", heart_rate)
    activity = min(1.0, max(0.0, (heart_rate - REST_HEART_RATE) / (EXERCISE_HEART_RATE - REST_HEART_RATE)))
    return (
        REST_O2_RATE + (EXERCISE_O2_RATE - REST_O2_RATE) * activity,
        REST_CO2_RATE + (EXERCISE_CO2_RATE - REST_CO2_RATE) * activity,
    )


def _countdown(now: float | None, limit: float, daily: float, *, upper: bool) -> float | None:
    if now is None:
        return None
    if (upper and now >= limit) or (not upper and now <= limit):
        return 0.0
    if (upper and daily > 0) or (not upper and daily < 0):
        return (limit - now) / daily * HOURS_PER_DAY
    return None


def step(
    state: CabinState,
    profile: Profile,
    crew_rates: Sequence[float | None],
    dt_seconds: float,
    *,
    o2_supply_failed: bool = False,
) -> Result:
    """Advance per-person heart rates; failure disables only O2 supply.

    Missing HR uses default rates; missing data is unknown unless a limit is reached.
    Negative gas pressures saturate at zero (exhaustion); constant rates are an
    approximation thereafter.
    """
    _validate_profile(profile)
    _validate_state(state, profile.total_pressure_mmhg)
    _nonnegative("dt_seconds", dt_seconds)
    if len(crew_rates) != profile.crew_count:
        raise ValueError("one heart rate (possibly None) is required per crew member")
    rates = tuple(metabolic_rates(hr) for hr in crew_rates)
    supply = 0.0 if o2_supply_failed else profile.o2_supply_kg_per_day
    o2_daily = pressure_change_mmhg(supply - sum(rate[0] for rate in rates), O2_MOLAR_MASS, profile)
    co2_daily = pressure_change_mmhg(
        sum(rate[1] for rate in rates) - profile.co2_removal_kg_per_day, CO2_MOLAR_MASS, profile
    )
    days = dt_seconds / SECONDS_PER_DAY
    updated = CabinState(
        None if state.pp_o2_mmhg is None else max(0.0, state.pp_o2_mmhg + o2_daily * days),
        None if state.pp_co2_mmhg is None else max(0.0, state.pp_co2_mmhg + co2_daily * days),
    )
    _validate_state(updated, profile.total_pressure_mmhg)
    o2_hours = _countdown(updated.pp_o2_mmhg, profile.o2_min_mmhg, o2_daily, upper=False)
    co2_hours = _countdown(updated.pp_co2_mmhg, profile.co2_max_mmhg, co2_daily, upper=True)
    missing = None in crew_rates or updated.pp_o2_mmhg is None or updated.pp_co2_mmhg is None
    status = "warning" if o2_hours == 0.0 or co2_hours == 0.0 else "unknown" if missing else "nominal"
    return Result(updated, o2_hours, co2_hours, status)


def load_ranges(path: str | Path) -> dict[str, float]:
    """Read PIO2 bounds from reference rule rows, never from cabin pp_o2 rules.

    Reference metric 'pio2' is not an API Metric; its delivery format still needs
    reference-owner confirmation. Comparators < / <= mark the lower bound and
    > / >= the upper bound. Missing or ambiguous bounds are rejected.
    Absent files raise FileNotFoundError.
    """
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, list):
        raise ValueError("ranges must be a list of reference rule rows")
    bounds = {"<": "pio2_min", "<=": "pio2_min", ">": "pio2_max", ">=": "pio2_max"}
    ranges: dict[str, float] = {}
    for rule in data:
        if rule["metric"] != "pio2" or rule["comparator"] not in bounds:
            continue
        key = bounds[rule["comparator"]]
        if key in ranges:
            raise ValueError(f"ambiguous PIO2 bound: {key}")
        value = float(rule["threshold"])
        _nonnegative(key, value)
        ranges[key] = value
    if ranges.keys() != {"pio2_min", "pio2_max"}:
        raise ValueError("PIO2 lower and upper reference bounds are required")
    if ranges["pio2_min"] > ranges["pio2_max"]:
        raise ValueError("pio2_min cannot exceed pio2_max")
    return ranges


def load_profile(path: str | Path, name: str) -> Profile:
    """Load a named profile, ignoring human-readable example notes."""
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)[name]
    names = {field.name for field in fields(Profile)}
    profile = Profile(**{key: value for key, value in data.items() if key in names})
    _validate_profile(profile)
    return profile
