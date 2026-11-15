"""Pure mass balance and instantaneous limit evaluation for the simulated atmosphere.

HYPOTHESIS: Estimating metabolic load from heart rate and the constant-flow
countdown are team models. The instantaneous CO2 comparison does not compute
the exposure averages used in the sources. The consecutive-measurement decision
lives outside this module; no medical diagnosis is produced.
"""

import json
import math
import operator
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

GAS_CONSTANT = 8.314
PASCALS_PER_MMHG = 133.322
GRAMS_PER_KG = 1000.0
O2_MOLAR_MASS = 32.0
CO2_MOLAR_MASS = 44.0
SECONDS_PER_DAY = 86400.0
HOURS_PER_DAY = 24.0


@dataclass(frozen=True)
class Rule:
    id: str
    comparator: str
    threshold: float
    source: str

    def matches(self, value: float | None, *, converted_threshold: float | None = None) -> bool:
        compare = {"<": operator.lt, ">": operator.gt}[self.comparator]
        threshold = self.threshold if converted_threshold is None else converted_threshold
        return value is not None and compare(value, threshold)


@dataclass(frozen=True)
class MetabolicRate:
    average: float
    minimum: float
    maximum: float


@dataclass(frozen=True)
class Metabolism:
    o2: MetabolicRate
    co2: MetabolicRate
    r_min: float
    r_max: float
    source: str


@dataclass(frozen=True)
class Ranges:
    rules: tuple[Rule, ...]
    metabolism: Metabolism
    temperature_k: float
    water_vapour_mmhg: float

    def rule(self, rule_id: str) -> Rule:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        raise ValueError(f"kurallar: {rule_id} missing")


@dataclass(frozen=True)
class Profile:
    volume_m3: float
    crew_count: int
    total_pressure_mmhg: float
    o2_supply_kg_per_day: float
    co2_removal_kg_per_day: float
    pio2_min_mmhg: float
    co2_max_mmhg: float
    limit_source: str
    ranges: Ranges

    @property
    def temperature_k(self) -> float:
        return self.ranges.temperature_k

    @property
    def o2_min_mmhg(self) -> float:
        return self.pp_o2_from_pio2(self.pio2_min_mmhg)

    def pp_o2_from_pio2(self, pio2_mmhg: float) -> float:
        total = self.total_pressure_mmhg
        return pio2_mmhg * total / (total - self.ranges.water_vapour_mmhg)


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
    pio2_mmhg: float | None
    consumption_source: str
    estimated: bool = True


def _number(name: str, value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name}: must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name}: {'positive' if positive else 'non-negative'}")
    return float(value)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name}: must be an object")
    return value


def _required(data: Mapping[str, object], key: str, name: str) -> object:
    if key not in data:
        raise ValueError(f"{name}.{key}: missing field")
    return data[key]


def _record(data: Mapping[str, object], key: str, name: str) -> Mapping[str, object]:
    return _mapping(_required(data, key, name), f"{name}.{key}")


def _field(data: Mapping[str, object], key: str, name: str, *, positive: bool = False) -> float:
    return _number(f"{name}.{key}", _required(data, key, name), positive=positive)


def _text(data: Mapping[str, object], key: str, name: str) -> str:
    value = _required(data, key, name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}.{key}: must be non-empty text")
    return value


def _read(path: str | Path) -> Mapping[str, object]:
    try:
        with open(path, encoding="utf-8") as stream:
            return _mapping(json.load(stream), "ranges")
    except json.JSONDecodeError as exc:
        raise ValueError(f"ranges: invalid JSON: {exc.msg}") from exc


def _load_rules(data: Mapping[str, object]) -> tuple[Rule, ...]:
    expected = {
        "pio2-hypoxia-low": ("pio2", "critical"),
        "pio2-normoxia-low": ("pio2", "caution"),
        "pio2-normoxia-high": ("pio2", "caution"),
        "ppco2-limit": ("pp_co2", "critical"),
        "ppco2-caution": ("pp_co2", "caution"),
    }
    rows = _required(data, "kurallar", "ranges")
    if not isinstance(rows, list):
        raise ValueError("kurallar: must be a list")
    rules: dict[str, Rule] = {}
    for row in rows:
        record = _mapping(row, "kurallar row")
        rule_id = _text(record, "id", "kurallar row")
        if rule_id not in expected:
            continue
        if rule_id in rules:
            raise ValueError(f"kurallar: duplicate id {rule_id}")
        metric, severity = expected[rule_id]
        if record.get("metric") != metric or record.get("severity") != severity:
            raise ValueError(f"kurallar.{rule_id}: wrong metric or severity")
        comparator = _text(record, "comparator", rule_id)
        if comparator not in ("<", ">"):
            raise ValueError(f"kurallar.{rule_id}.comparator: only < or > is supported")
        rules[rule_id] = Rule(
            rule_id,
            comparator,
            _field(record, "threshold", rule_id, positive=True),
            _text(record, "source", rule_id),
        )
    missing = expected.keys() - rules.keys()
    if missing:
        raise ValueError(f"kurallar: missing ids: {', '.join(sorted(missing))}")
    return tuple(rules.values())


def _load_metabolism(data: Mapping[str, object]) -> Metabolism:
    record = _record(data, "metabolik", "ranges")

    def rate(key: str) -> MetabolicRate:
        row = _record(record, key, "metabolik")
        result = MetabolicRate(
            *(_field(row, field, key, positive=True) for field in ("ortalama", "min", "max"))
        )
        if not result.minimum <= result.average <= result.maximum:
            raise ValueError(f"metabolik.{key}: must satisfy min <= ortalama <= max")
        return result

    scaling = _record(record, "nabiz_o2_olcekleme", "metabolik")
    r_min = _field(scaling, "r_min", "nabiz_o2_olcekleme", positive=True)
    r_max = _field(scaling, "r_max", "nabiz_o2_olcekleme", positive=True)
    if r_min >= r_max:
        raise ValueError("nabiz_o2_olcekleme: r_min must be less than r_max")
    sources = [
        _text(_record(record, key, "metabolik"), "kaynak", key) for key in ("o2_tuketimi", "co2_uretimi")
    ]
    source = "; ".join((*sources, f"HYPOTHESIS: {_text(scaling, 'kaynak', 'nabiz_o2_olcekleme')}"))
    return Metabolism(rate("o2_tuketimi"), rate("co2_uretimi"), r_min, r_max, source)


def _formula_number(text: str, pattern: str, name: str) -> float:
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise ValueError(f"{name}: reference formula must contain exactly one numeric value")
    return _number(name, float(matches[0].replace(",", ".")), positive=True)


def _load_ranges(data: Mapping[str, object]) -> Ranges:
    formula = _record(data, "kalan_sure_formulu", "ranges")
    temperature = _formula_number(
        _text(formula, "not", "kalan_sure_formulu"),
        r"\bT\s*=\s*(\d+(?:[.,]\d+)?)\s*K\b",
        "kalan_sure_formulu.not.T",
    )
    cabin = _record(data, "kabin", "ranges")
    pio2_formula = _record(cabin, "pio2_formulu", "kabin")
    vapour = _formula_number(
        _text(pio2_formula, "formul", "pio2_formulu"),
        r"\bPB\s*[-−]\s*(\d+(?:[.,]\d+)?)",
        "kabin.pio2_formulu.water_vapour",
    )
    return Ranges(_load_rules(data), _load_metabolism(data), temperature, vapour)


def load_ranges(path: str | Path) -> Ranges:
    return _load_ranges(_read(path))


def _validate_profile(profile: Profile) -> None:
    for name in ("volume_m3", "total_pressure_mmhg", "temperature_k"):
        _number(name, getattr(profile, name), positive=True)
    count = profile.crew_count
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("crew_count: must be a positive integer")
    for name in ("o2_supply_kg_per_day", "co2_removal_kg_per_day", "pio2_min_mmhg", "co2_max_mmhg"):
        _number(name, getattr(profile, name))
    if profile.total_pressure_mmhg <= profile.ranges.water_vapour_mmhg:
        raise ValueError("toplam_basinc_mmHg: must exceed the water vapour pressure")
    if max(profile.o2_min_mmhg, profile.co2_max_mmhg) > profile.total_pressure_mmhg:
        raise ValueError("profile: partial pressure limits cannot exceed total pressure")


def load_profile(path: str | Path, name: str) -> Profile:
    data = _read(path)
    ranges = _load_ranges(data)
    profiles = _record(data, "profiller", "ranges")
    row = _record(profiles, name, "profiller")
    o2_limit = _record(row, "o2_limit", name)
    co2_limit = _record(row, "co2_limit", name)
    profile = Profile(
        volume_m3=_field(row, "hacim_m3", name, positive=True),
        crew_count=_required(row, "kisi", name),
        total_pressure_mmhg=_field(row, "toplam_basinc_mmHg", name, positive=True),
        o2_supply_kg_per_day=_field(row, "o2_uretim_kg_gun", name),
        co2_removal_kg_per_day=_field(row, "co2_giderme_kg_gun", name),
        pio2_min_mmhg=_field(o2_limit, "pio2_min", f"{name}.o2_limit", positive=True),
        co2_max_mmhg=_field(co2_limit, "deger", f"{name}.co2_limit", positive=True),
        limit_source=(
            f"{_text(o2_limit, 'kaynak', 'o2_limit')}; {_text(co2_limit, 'kaynak', 'co2_limit')}; "
            "HYPOTHESIS: profile capacities and instantaneous limit evaluation"
        ),
        ranges=ranges,
    )
    _validate_profile(profile)
    return profile


def pressure_change_mmhg(mass_kg: float, molar_mass: float, profile: Profile) -> float:
    if not math.isfinite(mass_kg):
        raise ValueError("mass_kg: must be finite")
    _number("molar_mass", molar_mass, positive=True)
    return (
        mass_kg
        * GRAMS_PER_KG
        * GAS_CONSTANT
        * profile.temperature_k
        / (molar_mass * profile.volume_m3 * PASCALS_PER_MMHG)
    )


def _valid_measurement(value: float | None, *, positive: bool = True) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (value > 0 if positive else value >= 0)
    )


def metabolic_rates(
    heart_rate: float | None, baseline: float | None, metabolism: Metabolism
) -> tuple[float, float]:
    if not _valid_measurement(heart_rate) or not _valid_measurement(baseline):
        return metabolism.o2.average, metabolism.co2.average
    ratio = heart_rate / baseline
    activity = min(1.0, max(0.0, (ratio - metabolism.r_min) / (metabolism.r_max - metabolism.r_min)))
    return tuple(
        rate.minimum + (rate.maximum - rate.minimum) * activity for rate in (metabolism.o2, metabolism.co2)
    )


def _normalize_state(state: CabinState, total: float) -> tuple[CabinState, bool]:
    values = tuple(
        value if _valid_measurement(value, positive=False) else None
        for value in (state.pp_o2_mmhg, state.pp_co2_mmhg)
    )
    known = tuple(value for value in values if value is not None)
    if sum(known) <= total:
        return CabinState(*values), False
    largest = max(known)
    scaled_sum = sum(value / largest for value in known)
    normalized = tuple(None if value is None else value / largest * (total / scaled_sum) for value in values)
    return CabinState(*normalized), True


def _countdown(now: float | None, limit: float, daily: float, *, upper: bool) -> float | None:
    if now is None:
        return None
    remaining = limit - now if upper else now - limit
    if remaining <= 0:
        return 0.0
    towards_limit = daily if upper else -daily
    if towards_limit <= 0:
        return None
    return remaining / towards_limit * HOURS_PER_DAY


def _status(state: CabinState, profile: Profile, *, inconsistent: bool, missing: bool) -> str:
    ranges = profile.ranges

    def pio2_matches(rule_id: str) -> bool:
        rule = ranges.rule(rule_id)
        return rule.matches(state.pp_o2_mmhg, converted_threshold=profile.pp_o2_from_pio2(rule.threshold))

    if ranges.rule("ppco2-limit").matches(state.pp_co2_mmhg) or pio2_matches("pio2-hypoxia-low"):
        return "critical"
    if inconsistent or missing:
        return "unknown"
    if (
        ranges.rule("ppco2-caution").matches(state.pp_co2_mmhg)
        or pio2_matches("pio2-normoxia-low")
        or pio2_matches("pio2-normoxia-high")
    ):
        return "caution"
    return "nominal"


def step(
    state: CabinState,
    profile: Profile,
    crew_rates: Sequence[tuple[float | None, float | None]],
    dt_seconds: float,
    *,
    o2_supply_failed: bool = False,
) -> Result:
    _validate_profile(profile)
    _number("dt_seconds", dt_seconds)
    if len(crew_rates) != profile.crew_count:
        raise ValueError("crew_rates: one (heart rate, baseline) pair per crew member is required")
    if any(not isinstance(pair, tuple | list) or len(pair) != 2 for pair in crew_rates):
        raise ValueError("crew_rates: each entry must be a (heart rate, baseline) pair")
    missing = any(not _valid_measurement(value) for pair in crew_rates for value in pair)
    state, inconsistent_before = _normalize_state(state, profile.total_pressure_mmhg)
    rates = tuple(
        metabolic_rates(heart, baseline, profile.ranges.metabolism) for heart, baseline in crew_rates
    )
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
    updated, inconsistent_after = _normalize_state(updated, profile.total_pressure_mmhg)
    pio2 = (
        None
        if updated.pp_o2_mmhg is None
        else updated.pp_o2_mmhg
        * (profile.total_pressure_mmhg - profile.ranges.water_vapour_mmhg)
        / profile.total_pressure_mmhg
    )
    missing = missing or updated.pp_o2_mmhg is None or updated.pp_co2_mmhg is None
    status = _status(
        updated, profile, inconsistent=inconsistent_before or inconsistent_after, missing=missing
    )
    return Result(
        state=updated,
        hours_until_o2_limit=_countdown(updated.pp_o2_mmhg, profile.o2_min_mmhg, o2_daily, upper=False),
        hours_until_co2_limit=_countdown(updated.pp_co2_mmhg, profile.co2_max_mmhg, co2_daily, upper=True),
        status=status,
        pio2_mmhg=pio2,
        consumption_source=profile.ranges.metabolism.source,
    )
