"""Simulator logic is verified with independent, small JSON inputs."""

from __future__ import annotations

import json
import math
import random
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from backend import atmosphere, simulator
from backend.models import Scenario, TelemetryFrame
from backend.rules import RulesEngine, SepContext
from backend.simulator import SIMULATION_EPOCH, SepState, SimulationFinished, Simulator

REAL_DATA = Path(__file__).resolve().parents[1] / "data"
INTERVAL_MIN = 5
SECONDS_PER_MIN = 60
CONSECUTIVE_MEASUREMENTS = 3
CABIN_PCO2_MMHG = 2.0
PIO2_CAUTION_MMHG = 145
PIO2_CRITICAL_MMHG = 127
PIO2_RULES = {PIO2_CAUTION_MMHG: "cabin_pio2_low_caution", PIO2_CRITICAL_MMHG: "cabin_pio2_low_critical"}
BASELINES = {
    "heart_rate": (64.0, 2.0),
    "hrv_rmssd": (48.0, 8.0),
    "spo2": (98.0, 0.4),
    "resp_rate": (14.0, 1.0),
    "core_temp": (36.6, 0.1),
    "systolic_bp": (114.0, 6.0),
    "diastolic_bp": (74.0, 5.0),
}


@pytest.fixture
def scenario() -> dict[str, Any]:
    return {
        "scenario": "nominal",
        "seed": 42,
        "duration_min": 120,
        "model": {"measurement_interval_min": INTERVAL_MIN},
        "crew": [
            {
                "id": f"crew-{index + 1}",
                "name": f"Person {index + 1}",
                "role": "Commander",
                "baseline": {metric: {"mean": mean, "sd": sd} for metric, (mean, sd) in BASELINES.items()},
            }
            for index in range(4)
        ],
        "cabin_initial": {
            "pp_o2": {"value": 158.8, "unit": "mmHg"},
            "pp_co2": {"value": 2.0, "unit": "mmHg"},
            "cabin_pressure": {"value": 760.0, "unit": "mmHg"},
            "cabin_temp": {"value": 22.0, "unit": "°C"},
            "rel_humidity": {"value": 45.0, "unit": "%"},
            "radiation_dose_rate": {"value": 20.0, "unit": "µGy/h"},
        },
        "events": [],
    }


@pytest.fixture
def ranges_path(tmp_path: Path) -> Path:
    rules = [
        ("pio2-hypoxia-low", "pio2", "critical", "<", 127),
        ("pio2-normoxia-low", "pio2", "caution", "<", 145),
        ("pio2-normoxia-high", "pio2", "caution", ">", 155),
        ("ppco2-limit", "pp_co2", "critical", ">", 3),
        ("ppco2-caution", "pp_co2", "caution", ">", 2.5),
    ]
    data = {
        "kurallar": [
            dict(id=key, metric=metric, severity=severity, comparator=op, threshold=value, source="HIPOTEZ")
            for key, metric, severity, op, value in rules
        ],
        "metabolik": {
            "o2_tuketimi": {"ortalama": 0.84, "min": 0.49, "max": 1.25, "kaynak": "HIPOTEZ"},
            "co2_uretimi": {"ortalama": 1.0, "min": 0.52, "max": 1.5, "kaynak": "HIPOTEZ"},
            "nabiz_o2_olcekleme": {"r_min": 1, "r_max": 1.8, "kaynak": "HIPOTEZ"},
        },
        "kalan_sure_formulu": {"not": "T = 295 K"},
        "kabin": {"pio2_formulu": {"formul": "FIO2 * (PB - 47)"}},
        "profiller": {
            "mars_transit": {
                "hacim_m3": 75,
                "kisi": 4,
                "toplam_basinc_mmHg": 760,
                "o2_uretim_kg_gun": 3.4,
                "co2_giderme_kg_gun": 4.0,
                "o2_limit": {"pio2_min": 127, "kaynak": "HIPOTEZ"},
                "co2_limit": {"deger": 3.0, "kaynak": "HIPOTEZ"},
            }
        },
    }
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_simulator(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any], *, seed: int | None = None
) -> Simulator:
    path = tmp_path / f"{scenario['scenario']}.json"
    path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
    return Simulator(scenario["scenario"], seed=seed, scenario_dir=tmp_path, ranges_path=ranges_path)


def collect(simulator: Simulator) -> list[TelemetryFrame]:
    frames = []
    while not simulator.finished:
        frames.append(simulator.tick())
    return frames


def test_seed_reproducibility_and_global_random_isolation(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]
) -> None:
    global_state = random.getstate()
    first = collect(make_simulator(tmp_path, ranges_path, scenario))
    same = collect(make_simulator(tmp_path, ranges_path, scenario, seed=42))
    other = collect(make_simulator(tmp_path, ranges_path, scenario, seed=43))
    assert [frame.model_dump() for frame in first] == [frame.model_dump() for frame in same]
    assert [frame.model_dump() for frame in first] != [frame.model_dump() for frame in other]
    assert random.getstate() == global_state


@pytest.mark.parametrize("crew_order", [(3, 2, 1, 0), (1, 2, 3, 0)])
def test_per_person_random_streams_ignore_crew_order(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any], crew_order: tuple[int, ...]
) -> None:
    original = collect(make_simulator(tmp_path, ranges_path, scenario))
    scenario["crew"] = [scenario["crew"][index] for index in crew_order]
    reordered = collect(make_simulator(tmp_path, ranges_path, scenario))
    for before, after in zip(original, reordered, strict=True):
        assert {member.id: member for member in before.crew} == {member.id: member for member in after.crew}


def test_frame_contract_time_defaults_and_units(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]
) -> None:
    scenario["model"]["measurement_interval_min"] = 2
    frames = collect(make_simulator(tmp_path, ranges_path, scenario))
    for index, frame in enumerate(frames):
        assert TelemetryFrame.model_validate(frame.model_dump()) == frame
        assert frame.tick == index
        assert frame.mission_elapsed_s == index * 2 * SECONDS_PER_MIN
        assert frame.ts == SIMULATION_EPOCH + timedelta(seconds=frame.mission_elapsed_s)
        assert frame.contract_version == "0.2.0"
        assert frame.simulated is True
        assert frame.comms_one_way_delay_s == 0.0
        assert frame.alerts == frame.messages == []
        assert frame.cabin.status == "unknown"
        for metric in ("pp_o2", "pp_co2", "cabin_pressure"):
            reading = getattr(frame.cabin, metric)
            assert reading.unit == "mmHg"
        for metric in scenario["cabin_initial"]:
            reading = getattr(frame.cabin, metric)
            assert reading.ts == frame.ts
            assert reading.status == "unknown"
            assert reading.quality == "ok"
            assert reading.baseline_delta is None
        for member, person in zip(frame.crew, scenario["crew"], strict=True):
            assert (member.id, member.name, member.role) == (person["id"], person["name"], person["role"])
            assert member.activity == "resting"
            assert member.status == member.decision == "unknown"
            assert member.cumulative_dose_msv is member.event_dose_msv is None
            for metric, baseline in person["baseline"].items():
                reading = getattr(member.vitals, metric)
                assert reading.ts == frame.ts
                assert reading.status == "unknown"
                assert reading.quality == "ok"
                assert reading.baseline_delta == reading.value - baseline["mean"]
            assert member.vitals.systolic_bp.unit == member.vitals.diastolic_bp.unit == "mmHg"


def test_nominal_bounds_and_regulation(tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]) -> None:
    for frame in collect(make_simulator(tmp_path, ranges_path, scenario)):
        assert frame.cabin.pp_o2.value == 158.8
        assert frame.cabin.pp_co2.value == 2.0
        assert frame.cabin.hours_until_o2_limit is frame.cabin.hours_until_co2_limit is None
        for member in frame.crew:
            for metric, (mean, sd) in BASELINES.items():
                assert abs(getattr(member.vitals, metric).value - mean) < 6 * sd + 0.5


@pytest.mark.parametrize(
    "event_type,factor,falling", [("o2_generation_rate", 0.0, True), ("co2_removal_rate", 0.4, False)]
)
def test_independent_persistent_gas_failures(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any], event_type: str, factor: float, falling: bool
) -> None:
    scenario["events"] = [{"t_min": 7, "type": event_type, "value": factor}]
    frames = collect(make_simulator(tmp_path, ranges_path, scenario))
    assert frames[2].mission_elapsed_s == 10 * SECONDS_PER_MIN
    assert frames[2].cabin.pp_o2.value == 158.8
    assert frames[2].cabin.pp_co2.value == 2.0
    for before, after in zip(frames[2:], frames[3:], strict=False):
        if falling:
            assert after.cabin.pp_o2.value < before.cabin.pp_o2.value
            assert after.cabin.pp_co2.value == 2.0
            assert before.cabin.hours_until_o2_limit is not None
            assert after.cabin.hours_until_o2_limit is not None
            assert 0 < after.cabin.hours_until_o2_limit < before.cabin.hours_until_o2_limit
            assert after.cabin.hours_until_co2_limit is None
        else:
            assert after.cabin.pp_co2.value > before.cabin.pp_co2.value
            assert after.cabin.pp_o2.value == 158.8
            assert before.cabin.hours_until_co2_limit is not None
            assert after.cabin.hours_until_co2_limit is not None
            assert 0 < after.cabin.hours_until_co2_limit < before.cabin.hours_until_co2_limit
            assert after.cabin.hours_until_o2_limit is None


def test_nonzero_failure_multiplier_uses_original_capacity(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]
) -> None:
    scenario["events"] = [
        {"t_min": 0, "type": "o2_generation_rate", "value": 0.25},
        {"t_min": 0, "type": "co2_removal_rate", "value": 0.4},
    ]
    sim = make_simulator(tmp_path, ranges_path, scenario)
    sim.tick()
    second = sim.tick()
    profile = atmosphere.load_profile(ranges_path, "mars_transit")
    rates = [(person["baseline"]["heart_rate"]["mean"],) * 2 for person in scenario["crew"]]
    loads = [atmosphere.metabolic_rates(*pair, profile.ranges.metabolism) for pair in rates]
    days = INTERVAL_MIN * SECONDS_PER_MIN / atmosphere.SECONDS_PER_DAY
    expected_o2 = (
        158.8
        + atmosphere.pressure_change_mmhg(
            0.25 * profile.o2_supply_kg_per_day - sum(load[0] for load in loads),
            atmosphere.O2_MOLAR_MASS,
            profile,
        )
        * days
    )
    expected_co2 = (
        2
        + atmosphere.pressure_change_mmhg(
            sum(load[1] for load in loads) - 0.4 * profile.co2_removal_kg_per_day,
            atmosphere.CO2_MOLAR_MASS,
            profile,
        )
        * days
    )
    assert second.cabin.pp_o2.value == pytest.approx(expected_o2)
    assert second.cabin.pp_co2.value == pytest.approx(expected_co2)


@pytest.mark.parametrize("disturbance", ["NOISE_SCALE", "HEART_RATE_CIRCADIAN_AMPLITUDE"])
def test_reported_variation_does_not_change_atmosphere(
    tmp_path: Path,
    ranges_path: Path,
    scenario: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    disturbance: str,
) -> None:
    scenario["events"] = [
        {"t_min": 0, "type": "o2_generation_rate", "value": 0.0},
        {"t_min": 0, "type": "co2_removal_rate", "value": 0.4},
        {
            "t_min": 10,
            "type": "vital_ramp",
            "crew_id": "crew-1",
            "targets": {"heart_rate": 104},
            "ramp_min": 20,
        },
    ]
    monkeypatch.setattr(simulator, disturbance, 0.0)
    quiet = collect(make_simulator(tmp_path, ranges_path, scenario, seed=42))
    monkeypatch.setattr(simulator, disturbance, 1.0)
    noisy = collect(make_simulator(tmp_path, ranges_path, scenario, seed=42))
    assert [frame.cabin for frame in noisy] == [frame.cabin for frame in quiet]
    assert noisy[-1].cabin.pp_o2.value < noisy[0].cabin.pp_o2.value
    assert noisy[-1].cabin.pp_co2.value > noisy[0].cabin.pp_co2.value
    assert [frame.crew[0].vitals.heart_rate.value for frame in noisy] != [
        frame.crew[0].vitals.heart_rate.value for frame in quiet
    ]


@pytest.mark.parametrize("noise", [-1000.0, 1000.0])
def test_reported_vital_bounds_leave_metabolism_unchanged(
    tmp_path: Path,
    ranges_path: Path,
    scenario: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    noise: float,
) -> None:
    scenario["events"] = [
        {"t_min": 0, "type": "o2_generation_rate", "value": 0.0},
        {"t_min": 0, "type": "co2_removal_rate", "value": 0.4},
        {
            "t_min": 0,
            "type": "activity",
            "crew_id": "crew-1",
            "activity": "exercise",
            "targets": {"heart_rate": 104},
            "ramp_min": 0,
        },
    ]
    control = collect(make_simulator(tmp_path, ranges_path, scenario))
    monkeypatch.setattr(random.Random, "gauss", lambda self, mu, sigma: noise)
    frames = collect(make_simulator(tmp_path, ranges_path, scenario))
    assert [frame.cabin for frame in frames] == [frame.cabin for frame in control]
    for frame in frames:
        assert TelemetryFrame.model_validate(frame.model_dump()) == frame
        for member in frame.crew:
            assert member.vitals.spo2.value <= 100.0
            assert member.vitals.heart_rate.value >= 0.0
            assert member.vitals.resp_rate.value >= 0.0
            for metric in ("spo2", "heart_rate", "resp_rate"):
                reading = getattr(member.vitals, metric)
                assert reading.baseline_delta == reading.value - BASELINES[metric][0]
            if noise > 0:
                assert member.vitals.spo2.value == 100.0
            else:
                assert member.vitals.heart_rate.value == member.vitals.resp_rate.value == 0.0


@pytest.mark.parametrize(
    "crew_id,metric",
    [(None, metric) for metric in simulator.CABIN_UNITS]
    + [("crew-1", metric) for metric in simulator.VITAL_UNITS],
)
@pytest.mark.parametrize(
    "quality,values,expected_value",
    [
        ("missing", {}, None),
        ("stale", {"stale_value": 97.0}, 97.0),
        ("sensor_fault", {"reported_value": 12.0}, 12.0),
    ],
)
def test_sensor_quality_only_changes_target_reading(
    tmp_path: Path,
    ranges_path: Path,
    scenario: dict[str, Any],
    crew_id: str | None,
    metric: str,
    quality: str,
    values: dict[str, float],
    expected_value: float | None,
) -> None:
    scenario["duration_min"] = 30
    scenario["events"] = [
        {"t_min": 0, "type": "o2_generation_rate", "value": 0.0},
        {"t_min": 0, "type": "co2_removal_rate", "value": 0.4},
        {
            "t_min": 0,
            "type": "vital_ramp",
            "crew_id": "crew-1",
            "targets": {"heart_rate": 104},
            "ramp_min": 20,
        },
    ]
    control = collect(make_simulator(tmp_path, ranges_path, scenario))
    scenario["events"].append(
        {
            "t_min": 7,
            "type": "sensor_quality",
            "crew_id": crew_id,
            "metric": metric,
            "quality": quality,
            "until_min": 20,
            **values,
        }
    )
    sim = make_simulator(tmp_path, ranges_path, scenario)
    first_run = []
    for original in control:
        actual = sim.tick()
        first_run.append(actual.model_dump())
        assert TelemetryFrame.model_validate_json(actual.model_dump_json()) == actual
        assert sim._state.pp_o2_mmhg == original.cabin.pp_o2.value
        assert sim._state.pp_co2_mmhg == original.cabin.pp_co2.value
        assert actual.cabin.hours_until_o2_limit == original.cabin.hours_until_o2_limit
        assert actual.cabin.hours_until_co2_limit == original.cabin.hours_until_co2_limit
        assert actual.cabin.hours_until_o2_limit is not None
        assert actual.cabin.hours_until_co2_limit is not None
        if 7 <= actual.mission_elapsed_s / SECONDS_PER_MIN < 20:
            owner = actual.cabin if crew_id is None else actual.crew[0].vitals
            original_owner = original.cabin if crew_id is None else original.crew[0].vitals
            reading = getattr(owner, metric)
            assert reading.value == expected_value
            assert reading.quality == quality
            assert reading.status == "unknown"
            assert reading.baseline_delta is None
            setattr(owner, metric, getattr(original_owner, metric))
        assert actual == original
    sim.reset()
    assert [frame.model_dump() for frame in collect(sim)] == first_run


@pytest.mark.parametrize(
    "changes,expected_error",
    [
        ({"quality": "invalid"}, "quality"),
        ({"quality": None}, "quality"),
        ({"metric": "invalid"}, "metric"),
        ({"metric": "pp_co2"}, "metric"),
        ({"crew_id": None}, "metric"),
        ({"crew_id": "crew-9"}, "unknown crew member crew-9"),
        ({"until_min": 10}, "until_min"),
        ({"until_min": 9}, "until_min"),
        ({"until_min": None}, "until_min"),
        ({"value": None}, "value field"),
        ({"value": 1.0}, "value field"),
        ({"stale_value": 97.0}, "value field"),
        ({"reported_value": 12.0}, "value field"),
        ({"quality": "stale"}, "stale_value"),
        ({"quality": "stale", "stale_value": None}, "stale_value"),
        ({"quality": "sensor_fault"}, "reported_value"),
        ({"quality": "sensor_fault", "reported_value": None}, "reported_value"),
        ({"quality": "stale", "stale_value": math.nan}, "stale_value"),
        ({"quality": "sensor_fault", "reported_value": math.inf}, "reported_value"),
    ],
)
def test_invalid_sensor_quality_events_fail_explicitly(
    tmp_path: Path,
    ranges_path: Path,
    scenario: dict[str, Any],
    changes: dict[str, Any],
    expected_error: str,
) -> None:
    scenario["events"] = [
        {
            "t_min": 10,
            "type": "sensor_quality",
            "crew_id": "crew-1",
            "metric": "heart_rate",
            "quality": "missing",
            "until_min": 20,
            **changes,
        }
    ]
    with pytest.raises(ValueError, match=expected_error):
        make_simulator(tmp_path, ranges_path, scenario)


def test_activity_ramps_and_per_person_random_streams(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]
) -> None:
    control = collect(make_simulator(tmp_path, ranges_path, scenario))
    scenario["events"] = [
        {
            "t_min": 5,
            "type": "activity",
            "crew_id": "crew-1",
            "activity": "exercise",
            "targets": {"heart_rate": 124},
            "ramp_min": 10,
        },
        {"t_min": 15, "type": "vital_ramp", "crew_id": "crew-1", "targets": {"spo2": 92}, "ramp_min": 5},
        {
            "t_min": 25,
            "type": "activity",
            "crew_id": "crew-1",
            "activity": "resting",
            "targets": {"heart_rate": 64},
            "ramp_min": 10,
        },
    ]
    frames = collect(make_simulator(tmp_path, ranges_path, scenario))
    expected_offsets = {0: 0, 1: 0, 2: 30, 3: 60, 4: 60, 5: 60, 6: 30, 7: 0}
    for index, offset in expected_offsets.items():
        assert frames[index].crew[0].vitals.heart_rate.value - control[index].crew[
            0
        ].vitals.heart_rate.value == (pytest.approx(offset))
    assert frames[1].crew[0].activity == frames[4].crew[0].activity == "exercise"
    assert frames[5].crew[0].activity == "resting"
    assert frames[4].crew[0].vitals.spo2.value - control[4].crew[0].vitals.spo2.value == pytest.approx(-6)
    for actual, original in zip(frames, control, strict=True):
        assert actual.crew[1:] == original.crew[1:]
        assert actual.cabin.pp_co2.value == original.cabin.pp_co2.value
        assert actual.cabin.pp_o2.value == original.cabin.pp_o2.value
    changed = deepcopy(scenario)
    changed["crew"][0]["baseline"]["heart_rate"]["sd"] = 0
    for actual, modified in zip(frames, collect(make_simulator(tmp_path, ranges_path, changed)), strict=True):
        assert actual.crew[1:] == modified.crew[1:]


def test_interrupted_and_instant_ramps(tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]) -> None:
    control = collect(make_simulator(tmp_path, ranges_path, scenario))
    scenario["events"] = [
        {
            "t_min": 0,
            "type": "vital_ramp",
            "crew_id": "crew-1",
            "targets": {"heart_rate": 104},
            "ramp_min": 20,
        },
        {
            "t_min": 10,
            "type": "vital_ramp",
            "crew_id": "crew-1",
            "targets": {"heart_rate": 64},
            "ramp_min": 10,
        },
        {
            "t_min": 25,
            "type": "vital_ramp",
            "crew_id": "crew-1",
            "targets": {"heart_rate": 70},
            "ramp_min": 0,
        },
    ]
    frames = collect(make_simulator(tmp_path, ranges_path, scenario))
    for index, offset in enumerate([0, 10, 20, 10, 0, 6, 6]):
        assert frames[index].crew[0].vitals.heart_rate.value - control[index].crew[
            0
        ].vitals.heart_rate.value == (pytest.approx(offset))


def test_off_grid_ramp_starts_at_scheduled_minute(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]
) -> None:
    control = collect(make_simulator(tmp_path, ranges_path, scenario))
    scenario["events"] = [
        {
            "t_min": 2,
            "type": "vital_ramp",
            "crew_id": "crew-1",
            "targets": {"heart_rate": 104},
            "ramp_min": 10,
        }
    ]
    frames = collect(make_simulator(tmp_path, ranges_path, scenario))
    assert frames[0] == control[0]
    assert frames[1].crew[0].vitals.heart_rate.value - control[1].crew[0].vitals.heart_rate.value == (
        pytest.approx(12)
    )


@pytest.mark.parametrize("onset_min,end_min", [(5, 30), (0, 30), (2, 28), (5, None)])
def test_sep_state_lifecycle(
    tmp_path: Path,
    ranges_path: Path,
    scenario: dict[str, Any],
    onset_min: int,
    end_min: int | None,
) -> None:
    scenario["scenario"] = "solar_storm"
    scenario["duration_min"] = 40
    scenario["events"] = [
        {"t_min": onset_min, "type": "sep_onset"},
        {"t_min": 15, "type": "shelter"},
    ]
    if end_min is not None:
        scenario["events"].append({"t_min": end_min, "type": "sep_end"})
    sim = make_simulator(tmp_path, ranges_path, scenario)
    inactive = SepState(False, None, None)
    assert sim.sep_state == inactive
    first_active: SepState | None = None
    while not sim.finished:
        frame = sim.tick()
        minute = frame.mission_elapsed_s / SECONDS_PER_MIN
        state = sim.sep_state
        assert sim.sep_state == state
        if minute < onset_min or (end_min is not None and minute >= end_min):
            assert state == inactive
            if end_min is not None and end_min <= minute < end_min + INTERVAL_MIN:
                assert all(member.event_dose_msv is not None for member in frame.crew)
            continue
        assert state.active
        assert state.event_dose_rate_msv_per_h == (25.0 if minute < 15 else 6.25)
        if end_min is None:
            assert state.remaining_event_h is None
        else:
            assert state.remaining_event_h == pytest.approx((end_min - minute) / 60)
            assert state.remaining_event_h >= 0
        if first_active is None:
            first_active = state
        if minute == 15:
            sim.reset()
            assert sim.sep_state == inactive
            for _ in range(frame.tick + 1):
                sim.tick()
            assert sim.sep_state == state
    assert first_active is not None
    assert first_active.event_dose_rate_msv_per_h == 25.0
    with pytest.raises(FrozenInstanceError):
        first_active.active = False
    sim.reset()
    assert sim.sep_state == inactive


@pytest.mark.skipif(
    not (REAL_DATA / "scenarios" / "solar_storm.json").is_file()
    or not (REAL_DATA / "reference" / "ranges.json").is_file(),
    reason="Real solar_storm scenario or reference missing",
)
def test_real_solar_storm_sep_state_and_radiation_decision() -> None:
    sim = Simulator(Scenario.solar_storm)
    engine = RulesEngine()
    inactive = SepState(False, None, None)
    assert sim.sep_state == inactive
    critical_minutes: list[float] = []
    while not sim.finished:
        frame = sim.tick()
        minute = frame.mission_elapsed_s / SECONDS_PER_MIN
        state = sim.sep_state
        if 30 <= minute < 750:
            assert state.active
            assert state.event_dose_rate_msv_per_h == (25.0 if minute < 45 else 6.25)
            assert state.remaining_event_h == pytest.approx((750 - minute) / 60)
        else:
            assert state == inactive
        if minute == 750:
            assert all(member.event_dose_msv is not None for member in frame.crew)
        elif minute > 750:
            assert all(member.event_dose_msv is None for member in frame.crew)
        evaluated = engine.evaluate(
            frame,
            sep=SepContext(
                sep_active=state.active,
                event_dose_rate_msv_per_h=state.event_dose_rate_msv_per_h,
                remaining_event_h=state.remaining_event_h,
            ),
        )
        if state.active:
            assert all(member.decision != "unknown" for member in evaluated.crew)
        for alert in evaluated.alerts:
            if alert.rule_id == "karar-radyasyon_etkisi" and alert.severity == "critical":
                assert alert.crew_id in {member.id for member in frame.crew}
                assert alert.id == f"{alert.crew_id}:karar-radyasyon_etkisi"
                assert alert.why
                critical_minutes.append(minute)
    assert any(30 <= minute <= 45 for minute in critical_minutes)
    sim.reset()
    assert sim.sep_state == inactive


def test_solar_storm_dose_integration_and_reset(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]
) -> None:
    scenario["scenario"] = "solar_storm"
    scenario["events"] = [
        {"t_min": 5, "type": "sep_onset"},
        {"t_min": 15, "type": "shelter"},
        {"t_min": 30, "type": "sep_end"},
    ]
    sim = make_simulator(tmp_path, ranges_path, scenario)
    frames = collect(sim)
    assert frames[0].crew[0].event_dose_msv is None
    assert frames[0].cabin.radiation_dose_rate.value == 20
    assert frames[1].crew[0].event_dose_msv == 0
    assert frames[1].cabin.radiation_dose_rate.value == 16667
    assert frames[3].cabin.radiation_dose_rate.value == 16667 * 0.25
    for index in range(1, 7):
        minutes = index * INTERVAL_MIN
        expected = 25 * (min(minutes - 5, 10) + max(0, minutes - 15) * 0.25) / 60
        for member in frames[index].crew:
            assert member.event_dose_msv == pytest.approx(expected)
            assert member.cumulative_dose_msv is None
    for frame in frames[6:]:
        assert frame.cabin.radiation_dose_rate.value == 20
    for frame in frames[7:]:
        assert all(member.event_dose_msv is None for member in frame.crew)
    sim.reset()
    assert [frame.model_dump() for frame in collect(sim)] == [frame.model_dump() for frame in frames]


@pytest.mark.parametrize("initial_radiation", [0.0, 37.5])
def test_sep_end_restores_scenario_radiation(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any], initial_radiation: float
) -> None:
    scenario["scenario"] = "solar_storm"
    scenario["cabin_initial"]["radiation_dose_rate"]["value"] = initial_radiation
    scenario["duration_min"] = 15
    scenario["events"] = [
        {"t_min": 0, "type": "sep_onset"},
        {"t_min": 5, "type": "shelter"},
        {"t_min": 10, "type": "sep_end"},
    ]
    frames = collect(make_simulator(tmp_path, ranges_path, scenario))
    assert frames[0].cabin.radiation_dose_rate.value == 16667
    assert frames[1].cabin.radiation_dose_rate.value == 16667 * 0.25
    assert all(frame.cabin.radiation_dose_rate.value == initial_radiation for frame in frames[2:])


@pytest.mark.parametrize("duration,last_tick", [(10, 2), (12, 2)])
def test_finish_and_reset(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any], duration: int, last_tick: int
) -> None:
    scenario["duration_min"] = duration
    sim = make_simulator(tmp_path, ranges_path, scenario)
    assert not sim.finished
    first = sim.tick()
    rest = collect(sim)
    assert rest[-1].tick == last_tick
    assert sim.finished
    with pytest.raises(SimulationFinished, match="completed"):
        sim.tick()
    sim.reset()
    assert not sim.finished
    assert sim.tick().model_dump() == first.model_dump()


@pytest.mark.parametrize("scenario_name", ["sensor_dropout", "sleep_deprivation"])
def test_missing_scenario_in_configured_directory(
    tmp_path: Path, ranges_path: Path, scenario_name: str
) -> None:
    with pytest.raises(FileNotFoundError, match=scenario_name):
        Simulator(scenario_name, scenario_dir=tmp_path, ranges_path=ranges_path)


def test_missing_ranges(tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]) -> None:
    make_simulator(tmp_path, ranges_path, scenario)
    with pytest.raises(FileNotFoundError, match="missing"):
        Simulator(Scenario.nominal, scenario_dir=tmp_path, ranges_path=tmp_path / "missing.json")


@pytest.mark.parametrize(
    "event",
    [
        {"t_min": 0, "type": "unsupported"},
        {"t_min": 0, "type": "o2_generation_rate"},
        {"t_min": 0, "type": "vital_ramp", "crew_id": "crew-1"},
        {"t_min": 0, "type": "activity", "crew_id": "crew-1", "targets": {}, "ramp_min": 0},
        {"t_min": 0, "type": "vital_ramp", "crew_id": "crew-9", "targets": {}, "ramp_min": 0},
        {"t_min": 0, "type": "vital_ramp", "crew_id": "crew-1", "targets": {"typo": 5}, "ramp_min": 0},
        {"t_min": 0, "type": "co2_removal_rate", "value": math.nan},
    ],
)
def test_invalid_events_fail_explicitly(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any], event: dict[str, Any]
) -> None:
    scenario["events"] = [event]
    with pytest.raises(ValueError, match="events"):
        make_simulator(tmp_path, ranges_path, scenario)


@pytest.mark.parametrize("field", ["seed", "duration_min", "crew", "cabin_initial", "model", "events"])
def test_missing_required_fields(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any], field: str
) -> None:
    del scenario[field]
    with pytest.raises(ValueError, match=field):
        make_simulator(tmp_path, ranges_path, scenario)


def _pio2_mmhg(pp_o2_mmhg: float, total_mmhg: float, water_vapour_mmhg: float) -> float:
    return pp_o2_mmhg * (total_mmhg - water_vapour_mmhg) / total_mmhg


def _condition_minute(samples: list[tuple[float, float]], threshold: float, required: int) -> float | None:
    run = 0
    for minute, pio2 in samples:
        run = run + 1 if pio2 < threshold else 0
        if run == required:
            return minute
    return None


@pytest.mark.skipif(
    not (REAL_DATA / "scenarios" / "hypoxia.json").is_file()
    or not (REAL_DATA / "reference" / "ranges.json").is_file(),
    reason="Real hypoxia scenario or reference missing",
)
def test_real_hypoxia_pio2_crossing_times() -> None:
    scenario_data = json.loads((REAL_DATA / "scenarios" / "hypoxia.json").read_text(encoding="utf-8"))
    interval_min = scenario_data["model"]["measurement_interval_min"]
    alerts = {alert["rule_id"]: alert for alert in scenario_data["expected_alerts"]}
    profile = atmosphere.load_profile(REAL_DATA / "reference" / "ranges.json", "mars_transit")
    samples: list[tuple[float, float]] = []
    for frame in collect(Simulator(Scenario.hypoxia)):
        assert frame.cabin.pp_co2.value == CABIN_PCO2_MMHG
        pio2 = _pio2_mmhg(
            frame.cabin.pp_o2.value, profile.total_pressure_mmhg, profile.ranges.water_vapour_mmhg
        )
        samples.append((frame.mission_elapsed_s / SECONDS_PER_MIN, pio2))
    assert len(samples) > 1
    assert samples[1][0] - samples[0][0] == pytest.approx(interval_min)
    for threshold, rule_id in PIO2_RULES.items():
        condition_min = _condition_minute(samples, threshold, CONSECUTIVE_MEASUREMENTS)
        assert condition_min is not None, f"PIO2 never dropped below {threshold}"
        window = alerts[rule_id]
        assert window["t_earliest"] <= condition_min <= window["t_latest"], rule_id


@pytest.mark.skipif(
    not (REAL_DATA / "scenarios" / "hypercapnia.json").is_file()
    or not (REAL_DATA / "reference" / "ranges.json").is_file(),
    reason="Real hypercapnia scenario or reference missing",
)
def test_real_hypercapnia_co2_condition_windows() -> None:
    with (REAL_DATA / "scenarios" / "hypercapnia.json").open(encoding="utf-8") as stream:
        spec = json.load(stream)
    frames = collect(Simulator(Scenario.hypercapnia))
    interval = spec["model"]["measurement_interval_min"]
    window_samples = int(60 / interval)
    first_conditions: dict[str, float] = {}
    for index, frame in enumerate(frames):
        minute = frame.mission_elapsed_s / SECONDS_PER_MIN
        if frame.cabin.pp_co2.value > spec["thresholds"]["ppco2_yellow"]:
            first_conditions.setdefault("cabin_pp_co2_caution", minute)
        if index >= window_samples:
            values = [item.cabin.pp_co2.value for item in frames[index - window_samples : index + 1]]
            average = (sum(values) - (values[0] + values[-1]) / 2) / window_samples
            if average > spec["thresholds"]["ppco2_red_1h"]:
                first_conditions.setdefault("cabin_pp_co2_critical", minute)
    for expected in spec["expected_alerts"]:
        if expected["crew_id"] is None:
            minute = first_conditions[expected["rule_id"]]
            assert expected["t_earliest"] <= minute <= expected["t_latest"], (
                f"{expected['rule_id']}: condition at {minute:g} min, "
                f"expected window {expected['t_earliest']}..{expected['t_latest']} min; "
                "pre-fault CO2 regulation differs from the scenario's fixed removal calculation"
            )


@pytest.mark.skipif(
    not (REAL_DATA / "scenarios" / "sensor_dropout.json").is_file()
    or not (REAL_DATA / "reference" / "ranges.json").is_file(),
    reason="PR #36 sensor_dropout scenario or reference missing",
)
def test_real_sensor_dropout_readings_and_physics(tmp_path: Path) -> None:
    with (REAL_DATA / "scenarios" / "sensor_dropout.json").open(encoding="utf-8") as stream:
        spec = json.load(stream)
    control_spec = deepcopy(spec)
    control_spec["events"] = [event for event in spec["events"] if event["type"] != "sensor_quality"]
    control = collect(make_simulator(tmp_path, REAL_DATA / "reference" / "ranges.json", control_spec))
    sim = Simulator(Scenario.sensor_dropout)
    frames: dict[float, TelemetryFrame] = {}
    for original in control:
        frame = sim.tick()
        minute = frame.mission_elapsed_s / SECONDS_PER_MIN
        frames[minute] = frame
        assert TelemetryFrame.model_validate_json(frame.model_dump_json()) == frame
        assert sim._state.pp_co2_mmhg == pytest.approx(2.0)
        assert sim._state.pp_o2_mmhg == original.cabin.pp_o2.value
        assert frame.cabin.pp_o2 == original.cabin.pp_o2
        assert frame.cabin.hours_until_o2_limit == original.cabin.hours_until_o2_limit
        assert frame.cabin.hours_until_co2_limit == original.cabin.hours_until_co2_limit
        assert frame.crew[2] == original.crew[2]
        affected = [
            (frame.crew[1].vitals.heart_rate, original.crew[1].vitals.heart_rate, 30, 60, "missing", None),
            (frame.crew[3].vitals.spo2, original.crew[3].vitals.spo2, 70, 90, "stale", 97.0),
            (frame.cabin.pp_co2, original.cabin.pp_co2, 100, 115, "sensor_fault", 12.0),
        ]
        for reading, normal, start, until, quality, value in affected:
            if start <= minute < until:
                assert reading.value == value
                assert reading.quality == quality
                assert reading.status == "unknown"
                assert reading.baseline_delta is None
            else:
                assert reading == normal
    assert frames[60].crew[1].vitals.heart_rate.quality == "ok"
    assert frames[90].crew[3].vitals.spo2.quality == "ok"
    assert frames[115].cabin.pp_co2.quality == "ok"
    assert frames[115].cabin.pp_co2.value == pytest.approx(2.0)
    assert sim.finished


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            name,
            marks=pytest.mark.skipif(
                not (REAL_DATA / "scenarios" / f"{name}.json").is_file()
                or not (REAL_DATA / "reference" / "ranges.json").is_file(),
                reason="Real scenario or reference missing",
            ),
        )
        for name in ("nominal", "hypercapnia", "solar_storm")
    ],
)
def test_real_scenario_smoke(name: str) -> None:
    with (REAL_DATA / "scenarios" / f"{name}.json").open(encoding="utf-8") as stream:
        spec = json.load(stream)
    sim = Simulator(name)
    expected_count = math.floor(spec["duration_min"] / spec["model"]["measurement_interval_min"]) + 1
    assert len(collect(sim)) == expected_count
    assert sim.finished


def test_emitter_units_follow_english_contract() -> None:
    from backend.main import Pipeline
    from backend.models import METRIC_UNITS, Scenario
    from backend.simulator import SimulationFinished

    pipe = Pipeline(Scenario.nominal)
    frame = None
    try:
        while True:
            frame = pipe.step()
    except SimulationFinished:
        pass
    assert frame is not None
    assert frame.crew[0].vitals.resp_rate.unit == METRIC_UNITS["resp_rate"]
    assert frame.cabin.radiation_dose_rate.unit == METRIC_UNITS["radiation_dose_rate"]


def test_simulator_tracks_atmosphere_status_pio2_and_o2_supply_failed(
    tmp_path: Path, ranges_path: Path, scenario: dict[str, Any]
) -> None:
    scenario["duration_min"] = 30
    scenario["model"]["measurement_interval_min"] = 5
    scenario["events"] = [
        {"t_min": 5, "type": "o2_generation_rate", "value": 0.0},
    ]
    sim = make_simulator(tmp_path, ranges_path, scenario)
    frames = collect(sim)
    assert frames[0].cabin.status == "unknown"
    assert sim.last_atmosphere_status is not None
    assert sim.last_pio2_mmhg is not None
    assert frames[-1].cabin.pp_o2.value is not None
    assert frames[-1].cabin.pp_o2.value < frames[0].cabin.pp_o2.value
