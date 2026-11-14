"""Deterministik, simüle telemetri; karar ve uyarıları rules.py üretir."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend import atmosphere
from backend.models import (
    CREW_SIZE,
    Activity,
    CabinAtmosphere,
    CrewMember,
    CrewVitals,
    Reading,
    Scenario,
    TelemetryFrame,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SIMULATION_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
SECONDS_PER_MINUTE = 60.0
MINUTES_PER_HOUR = 60.0
CIRCADIAN_PERIOD_MIN = 1440.0
NOISE_SCALE = 1.0
# HIPOTEZ: küçük sirkadiyen salınım; klinik model değildir.
HEART_RATE_CIRCADIAN_AMPLITUDE = 0.5
CORE_TEMP_CIRCADIAN_AMPLITUDE = 0.05
# senaryo notundan, HIPOTEZ, Baris sayisal alan eklemeli.
SEP_RADIATION_UGY_PER_HOUR = 16667.0
SHELTER_FACTOR = 0.25
SEP_EFFECTIVE_MSV_PER_HOUR = 25.0

VITAL_UNITS = {
    "heart_rate": "bpm",
    "hrv_rmssd": "ms",
    "spo2": "%",
    "resp_rate": "nefes/dk",
    "core_temp": "°C",
    "systolic_bp": "mmHg",
    "diastolic_bp": "mmHg",
}
CABIN_UNITS = {
    "pp_o2": "mmHg",
    "pp_co2": "mmHg",
    "cabin_pressure": "mmHg",
    "cabin_temp": "°C",
    "rel_humidity": "%",
    "radiation_dose_rate": "µGy/saat",
}


class SimulationFinished(Exception):
    """Senaryonun son karesi zaten üretildi."""


class _Input(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class _Baseline(_Input):
    mean: float
    sd: float = Field(ge=0)


class _Person(_Input):
    id: str = Field(pattern=r"^crew-[1-9]$")
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    baseline: dict[str, _Baseline]

    @model_validator(mode="after")
    def _all_vitals(self) -> _Person:
        if self.baseline.keys() != VITAL_UNITS.keys():
            raise ValueError("baseline: yedi vital alanı gerekli: " + ", ".join(VITAL_UNITS))
        if self.baseline["heart_rate"].mean <= 0:
            raise ValueError("baseline.heart_rate.mean: pozitif olmalı")
        return self


class _InitialReading(_Input):
    value: float = Field(ge=0)
    unit: str


class _Timing(_Input):
    measurement_interval_min: float = Field(gt=0)


class _Event(_Input):
    t_min: float = Field(ge=0)
    type: Literal[
        "vital_ramp", "activity", "o2_generation_rate", "co2_removal_rate", "sep_onset", "shelter", "sep_end"
    ]
    crew_id: str | None = None
    activity: Activity | None = None
    targets: dict[str, float] | None = None
    ramp_min: float | None = Field(default=None, ge=0)
    value: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _required_fields(self) -> _Event:
        if self.type in ("vital_ramp", "activity"):
            if self.crew_id is None or self.targets is None or self.ramp_min is None:
                raise ValueError(f"{self.type}: crew_id, targets ve ramp_min gerekli")
            if self.targets.keys() - VITAL_UNITS.keys():
                raise ValueError(f"{self.type}.targets: bilinmeyen vital")
            if self.type == "activity" and self.activity is None:
                raise ValueError("activity.activity: gerekli alan")
        if self.type in ("o2_generation_rate", "co2_removal_rate") and self.value is None:
            raise ValueError(f"{self.type}.value: gerekli alan")
        return self


class _Scenario(_Input):
    scenario: Scenario
    seed: int
    duration_min: float = Field(gt=0)
    model: _Timing
    crew: list[_Person] = Field(min_length=CREW_SIZE, max_length=CREW_SIZE)
    cabin_initial: dict[str, _InitialReading]
    events: list[_Event]

    @model_validator(mode="after")
    def _references(self) -> _Scenario:
        ids = {person.id for person in self.crew}
        if len(ids) != CREW_SIZE:
            raise ValueError("crew.id: kimlikler benzersiz olmalı")
        if self.cabin_initial.keys() != CABIN_UNITS.keys():
            raise ValueError("cabin_initial: altı kabin okuması gerekli: " + ", ".join(CABIN_UNITS))
        for metric, unit in CABIN_UNITS.items():
            if self.cabin_initial[metric].unit != unit:
                raise ValueError(f"cabin_initial.{metric}.unit: {unit} olmalı")
        for event in self.events:
            if event.type in ("vital_ramp", "activity") and event.crew_id not in ids:
                raise ValueError(f"events.crew_id: bilinmeyen kişi {event.crew_id}")
        return self


@dataclass(frozen=True)
class _Ramp:
    start_min: float
    duration_min: float
    start: float
    target: float

    def at(self, minute: float) -> float:
        fraction = 1.0 if self.duration_min == 0 else (minute - self.start_min) / self.duration_min
        return self.start + (self.target - self.start) * min(1.0, max(0.0, fraction))


class Simulator:
    """Bir tick bir ölçüm anıdır; duration_min sonundaki kare dahildir.

    Olaylar ilk uygun ölçüm anında etkinleşir. Gaz ve doz integrasyonu önceki
    karenin akışlarıyla [önceki an, şimdiki an) aralığında yapılır. Gürültü
    rampanın durumuna veya metabolizmaya beslenmez; yalnızca üretilen okumaya eklenir.
    """

    def __init__(
        self,
        scenario: Scenario | str,
        *,
        seed: int | None = None,
        scenario_dir: Path = DATA_DIR / "scenarios",
        ranges_path: Path = DATA_DIR / "reference" / "ranges.json",
    ) -> None:
        self.scenario = Scenario(scenario)
        path = Path(scenario_dir) / f"{self.scenario.value}.json"
        with path.open(encoding="utf-8") as stream:
            self._spec = _Scenario.model_validate(json.load(stream))
        if self._spec.scenario != self.scenario:
            raise ValueError(f"{path}: scenario alanı {self.scenario.value} olmalı")
        self._seed = self._spec.seed if seed is None else seed
        ranges = atmosphere.load_ranges(ranges_path)
        self._profile = replace(atmosphere.load_profile(ranges_path, "mars_transit"), ranges=ranges)
        if self._profile.crew_count != len(self._spec.crew):
            raise ValueError("mars_transit.crew_count: senaryo kişi sayısıyla eşleşmeli")
        pressure = self._spec.cabin_initial["cabin_pressure"].value
        if pressure != self._profile.total_pressure_mmhg:
            raise ValueError("cabin_initial.cabin_pressure: mars_transit toplam basıncıyla eşleşmeli")
        self._events = sorted(self._spec.events, key=lambda event: event.t_min)
        self.reset()

    @property
    def finished(self) -> bool:
        """Sıradaki ölçüm anı senaryonun süresini aştı mı?"""
        return self._tick * self._spec.model.measurement_interval_min > self._spec.duration_min

    def reset(self) -> None:
        """Dosyaları tekrar okumadan tüm durum ve kişi RNG'lerini başa alır."""
        self._tick = 0
        self._event_index = 0
        self._random = {person.id: Random(f"{self._seed}:{person.id}") for person in self._spec.crew}
        self._ramps: dict[str, dict[str, _Ramp]] = {person.id: {} for person in self._spec.crew}
        self._activities = {person.id: Activity.resting for person in self._spec.crew}
        self._o2_factor: float | None = None
        self._co2_factor: float | None = None
        self._sep_active = False
        self._sep_ended_this_tick = False
        self._sheltered = False
        self._event_doses = {person.id: 0.0 for person in self._spec.crew}
        self._state = atmosphere.CabinState(
            self._spec.cabin_initial["pp_o2"].value,
            self._spec.cabin_initial["pp_co2"].value,
        )
        self._previous_rates: list[tuple[float | None, float | None]] = []

    def _apply_events(self, minute: float) -> None:
        while self._event_index < len(self._events):
            event = self._events[self._event_index]
            if event.t_min > minute:
                break
            self._apply_event(event)
            self._event_index += 1

    def _apply_event(self, event: _Event) -> None:
        if event.type in ("activity", "vital_ramp"):
            person = next(person for person in self._spec.crew if person.id == event.crew_id)
            if event.type == "activity":
                self._activities[person.id] = event.activity
            for metric, target in event.targets.items():
                current = self._vital_mean(person, metric, event.t_min)
                self._ramps[person.id][metric] = _Ramp(event.t_min, event.ramp_min, current, target)
        elif event.type == "o2_generation_rate":
            self._o2_factor = event.value
        elif event.type == "co2_removal_rate":
            self._co2_factor = event.value
        elif event.type == "sep_onset":
            self._sep_active = True
            self._sheltered = False
            self._event_doses = dict.fromkeys(self._event_doses, 0.0)
        elif event.type == "shelter":
            self._sheltered = True
        elif event.type == "sep_end":
            if self._sep_active:
                self._sep_ended_this_tick = True
            self._sep_active = False
            self._sheltered = False
        else:
            raise ValueError(f"Desteklenmeyen olay tipi: {event.type}")

    def _vital_mean(self, person: _Person, metric: str, minute: float) -> float:
        ramp = self._ramps[person.id].get(metric)
        return person.baseline[metric].mean if ramp is None else ramp.at(minute)

    def _physiological_vital(self, person: _Person, metric: str, minute: float) -> float:
        wave = math.sin(math.tau * minute / CIRCADIAN_PERIOD_MIN)
        amplitudes = {
            "heart_rate": HEART_RATE_CIRCADIAN_AMPLITUDE,
            "core_temp": CORE_TEMP_CIRCADIAN_AMPLITUDE,
        }
        return self._vital_mean(person, metric, minute) + amplitudes.get(metric, 0.0) * wave

    def _crew(self, minute: float, ts: datetime) -> list[CrewMember]:
        crew = []
        for person in self._spec.crew:
            readings = {}
            for metric, unit in VITAL_UNITS.items():
                baseline = person.baseline[metric]
                value = self._physiological_vital(person, metric, minute) + self._random[person.id].gauss(
                    0.0, baseline.sd * NOISE_SCALE
                )
                readings[metric] = Reading(
                    value=value, unit=unit, ts=ts, baseline_delta=value - baseline.mean
                )
            crew.append(
                CrewMember(
                    id=person.id,
                    name=person.name,
                    role=person.role,
                    vitals=CrewVitals(**readings),
                    activity=self._activities[person.id],
                    # Bitiş karesi son aralığın nihai dozunu taşır; sonraki karelerde olay yoktur.
                    event_dose_msv=(
                        self._event_doses[person.id]
                        if self._sep_active or self._sep_ended_this_tick
                        else None
                    ),
                )
            )
        return crew

    def _regulated_profile(self, rates: list[tuple[float | None, float | None]]) -> atmosphere.Profile:
        loads = [
            atmosphere.metabolic_rates(heart, baseline, self._profile.ranges.metabolism)
            for heart, baseline in rates
        ]
        return replace(
            self._profile,
            o2_supply_kg_per_day=(
                sum(load[0] for load in loads)
                if self._o2_factor is None
                else self._o2_factor * self._profile.o2_supply_kg_per_day
            ),
            co2_removal_kg_per_day=(
                sum(load[1] for load in loads)
                if self._co2_factor is None
                else self._co2_factor * self._profile.co2_removal_kg_per_day
            ),
        )

    def _advance(self, dt_seconds: float) -> None:
        result = atmosphere.step(
            self._state, self._regulated_profile(self._previous_rates), self._previous_rates, dt_seconds
        )
        self._state = result.state
        if self._sep_active:
            factor = SHELTER_FACTOR if self._sheltered else 1.0
            dose = SEP_EFFECTIVE_MSV_PER_HOUR * factor * dt_seconds / (SECONDS_PER_MINUTE * MINUTES_PER_HOUR)
            for person_id in self._event_doses:
                self._event_doses[person_id] += dose

    def _cabin(self, result: atmosphere.Result, ts: datetime) -> CabinAtmosphere:
        values = {metric: reading.value for metric, reading in self._spec.cabin_initial.items()}
        values.update(pp_o2=result.state.pp_o2_mmhg, pp_co2=result.state.pp_co2_mmhg)
        if self._sep_active:
            values["radiation_dose_rate"] = SEP_RADIATION_UGY_PER_HOUR * (
                SHELTER_FACTOR if self._sheltered else 1.0
            )
        return CabinAtmosphere(
            **{
                metric: Reading(value=value, unit=CABIN_UNITS[metric], ts=ts)
                for metric, value in values.items()
            },
            hours_until_o2_limit=result.hours_until_o2_limit,
            hours_until_co2_limit=result.hours_until_co2_limit,
        )

    def tick(self) -> TelemetryFrame:
        """Bir kare üretir; süre aşıldığında SimulationFinished fırlatır."""
        if self.finished:
            raise SimulationFinished(f"{self.scenario.value}: {self._spec.duration_min:g} dakika tamamlandı")
        interval = self._spec.model.measurement_interval_min
        minute = self._tick * interval
        if self._tick:
            self._advance(interval * SECONDS_PER_MINUTE)
        self._sep_ended_this_tick = False
        self._apply_events(minute)
        elapsed = minute * SECONDS_PER_MINUTE
        ts = SIMULATION_EPOCH + timedelta(seconds=elapsed)
        crew = self._crew(minute, ts)
        rates = [
            (self._physiological_vital(person, "heart_rate", minute), person.baseline["heart_rate"].mean)
            for person in self._spec.crew
        ]
        result = atmosphere.step(self._state, self._regulated_profile(rates), rates, 0.0)
        frame = TelemetryFrame(
            tick=self._tick,
            ts=ts,
            mission_elapsed_s=elapsed,
            scenario=self.scenario,
            simulated=True,
            crew=crew,
            cabin=self._cabin(result, ts),
            comms_one_way_delay_s=0.0,  # Gecikmeyi comms.py ekler.
        )
        self._state = result.state
        self._previous_rates = rates
        self._tick += 1
        return frame
