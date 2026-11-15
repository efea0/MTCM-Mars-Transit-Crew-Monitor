"""Single source of truth: the entire API contract is derived from these Pydantic v2 models.

After changing anything, regenerate the contract:
    python -m backend.models
Contract changes are a joint decision of Barış and Efe.

Core principles:
- Missing data is never treated as "nominal": a reading with value=None carries
  quality != ok and its status is unknown.
- Every alert (Alert) carries at least one piece of evidence (Evidence) and is
  triggered by consecutive measurements.
- Numeric thresholds live in rules.py, not here. This module defines structure only.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "0.2.0"
CREW_SIZE = 4


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Status(str, Enum):
    """Overall status of a measurement, a crew member, or the cabin."""

    nominal = "nominal"
    caution = "caution"
    warning = "warning"
    critical = "critical"
    unknown = "unknown"


class CrewDecision(str, Enum):
    """Per-crew-member decision: normal = Normal, watch = Watch, action = Action, unknown = Unknown."""

    normal = "normal"
    watch = "watch"
    action = "action"
    unknown = "unknown"


class Severity(str, Enum):
    caution = "caution"
    warning = "warning"
    critical = "critical"


class Quality(str, Enum):
    ok = "ok"
    stale = "stale"
    missing = "missing"
    sensor_fault = "sensor_fault"


class Comparator(str, Enum):
    lt = "<"
    le = "<="
    gt = ">"
    ge = ">="
    missing = "missing"


class Metric(str, Enum):
    heart_rate = "heart_rate"  # bpm
    hrv_rmssd = "hrv_rmssd"  # ms
    spo2 = "spo2"  # %
    resp_rate = "resp_rate"  # breaths/min
    core_temp = "core_temp"  # °C
    systolic_bp = "systolic_bp"  # mmHg
    diastolic_bp = "diastolic_bp"  # mmHg
    pp_co2 = "pp_co2"  # mmHg
    pp_o2 = "pp_o2"  # mmHg (cabin partial pressure; not PIO₂)
    cabin_pressure = "cabin_pressure"  # mmHg
    cabin_temp = "cabin_temp"  # °C
    rel_humidity = "rel_humidity"  # %
    radiation_dose_rate = "radiation_dose_rate"  # µGy/h
    pio2 = "pio2"  # mmHg, inspired O₂ partial pressure (derived from cabin pp_o2)
    event_dose = "event_dose"  # mSv, dose received during an active solar particle event


METRIC_UNITS: dict[Metric, str] = {
    Metric.heart_rate: "bpm",
    Metric.hrv_rmssd: "ms",
    Metric.spo2: "%",
    Metric.resp_rate: "breaths/min",
    Metric.core_temp: "°C",
    Metric.systolic_bp: "mmHg",
    Metric.diastolic_bp: "mmHg",
    Metric.pp_co2: "mmHg",
    Metric.pp_o2: "mmHg",
    Metric.cabin_pressure: "mmHg",
    Metric.cabin_temp: "°C",
    Metric.rel_humidity: "%",
    Metric.radiation_dose_rate: "µGy/h",
    Metric.pio2: "mmHg",
    Metric.event_dose: "mSv",
}


class Scenario(str, Enum):
    nominal = "nominal"
    hypoxia = "hypoxia"
    hypercapnia = "hypercapnia"
    sleep_deprivation = "sleep_deprivation"
    solar_storm = "solar_storm"
    sensor_dropout = "sensor_dropout"


class Activity(str, Enum):
    """Astronaut's current activity. Raised heart/respiratory rate during exercise
    is not counted as an alarm or evidence."""

    resting = "resting"
    exercise = "exercise"
    sleep = "sleep"
    unknown = "unknown"


class Sender(str, Enum):
    crew = "crew"
    mission_control = "mission_control"
    system = "system"


class Reading(_Model):
    """A single sensor reading. If value=None, quality cannot be 'ok'."""

    value: float | None = Field(description="Measured value; null if no data.")
    unit: str = Field(description="E.g. 'bpm', '%', 'mmHg'.")
    ts: datetime = Field(description="Measurement time (UTC, ISO-8601).")
    quality: Quality = Quality.ok
    status: Status = Field(
        default=Status.unknown,
        description="Filled in by rules.py; unknown if not evaluated.",
    )
    baseline_delta: float | None = Field(
        default=None,
        description="Crew vitals only: value minus the person's own baseline (in the reading's unit). "
        "Null for cabin readings and when there is no data.",
    )

    @model_validator(mode="after")
    def _missing_is_never_normal(self) -> Reading:
        if self.value is None:
            if self.quality == Quality.ok.value:
                raise ValueError("quality cannot be 'ok' when value=None")
            if self.status != Status.unknown.value:
                raise ValueError("status can only be 'unknown' when value=None")
            if self.baseline_delta is not None:
                raise ValueError("baseline_delta must be null when value=None")
        elif self.quality != Quality.ok.value and self.status == Status.nominal.value:
            raise ValueError("a reading whose quality is not 'ok' cannot be counted as 'nominal'")
        return self


class CrewVitals(_Model):
    heart_rate: Reading
    hrv_rmssd: Reading
    spo2: Reading
    resp_rate: Reading
    core_temp: Reading
    systolic_bp: Reading
    diastolic_bp: Reading


class CabinAtmosphere(_Model):
    pp_co2: Reading
    pp_o2: Reading
    cabin_pressure: Reading
    cabin_temp: Reading
    rel_humidity: Reading
    radiation_dose_rate: Reading
    status: Status = Status.unknown
    hours_until_o2_limit: float | None = Field(
        default=None,
        ge=0,
        description="Time until the O₂ limit (hours); None = not approaching it or not computable; "
        "0.0 = limit already exceeded.",
    )
    hours_until_co2_limit: float | None = Field(
        default=None,
        ge=0,
        description="Time until the CO₂ limit (hours); None = not approaching it or not computable; "
        "0.0 = limit already exceeded.",
    )

    @model_validator(mode="after")
    def _cabin_has_no_baseline_delta(self) -> CabinAtmosphere:
        for field in (
            "pp_co2",
            "pp_o2",
            "cabin_pressure",
            "cabin_temp",
            "rel_humidity",
            "radiation_dose_rate",
        ):
            if getattr(self, field).baseline_delta is not None:
                raise ValueError(f"{field}: baseline_delta must be null on a cabin reading")
        return self


class CrewMember(_Model):
    id: str = Field(pattern=r"^crew-[1-9]$", description="E.g. 'crew-1'.")
    name: str
    role: str = Field(description="E.g. 'Commander', 'Flight Surgeon'.")
    vitals: CrewVitals
    activity: Activity = Activity.unknown
    cumulative_dose_msv: float | None = Field(
        default=None,
        ge=0,
        description="Career cumulative effective dose (mSv, simulated); vs. the 600 mSv career limit.",
    )
    event_dose_msv: float | None = Field(
        default=None,
        ge=0,
        description="Dose received during an active solar particle event (mSv, simulated); null if no event.",
    )
    status: Status = Status.unknown
    decision: CrewDecision = Field(
        default=CrewDecision.unknown,
        description="Per-crew-member decision; set by rules.py. unknown if not evaluated or data missing.",
    )

    @model_validator(mode="after")
    def _missing_vitals_require_unknown_decision(self) -> CrewMember:
        if (
            all(getattr(self.vitals, field).value is None for field in CrewVitals.model_fields)
            and self.decision != CrewDecision.unknown
        ):
            raise ValueError(
                "missing data is never normal; decision can only be unknown when all vitals are missing"
            )
        return self


class Evidence(_Model):
    """A single evidence item in an alert's 'why?' list."""

    metric: Metric
    value: float | None
    threshold: float | None = Field(description="Limit that was crossed; null for missing-data evidence.")
    comparator: Comparator
    ts: datetime
    note: str = Field(
        description="Human-readable explanation, e.g. 'SpO2 below threshold for 3 measurements'."
    )


class ThresholdRule(_Model):
    """Rule structure. Numeric values and their sources are defined in rules.py."""

    id: str
    metric: Metric
    severity: Severity
    comparator: Comparator
    threshold: float | None = Field(description="Null if comparator=missing.")
    consecutive_required: int = Field(
        ge=1, description="Number of consecutive measurements required to raise an alert."
    )
    average_window_s: float | None = Field(
        default=None,
        gt=0,
        description="Averaging window (s); e.g. 3600 for CO₂ = 1-hour average. null = instantaneous value.",
    )
    relative_to_baseline: bool = Field(
        default=False,
        description="If true, the compared quantity is 'value minus the person's baseline' and threshold is "
        "read as a difference in the same unit (e.g. heart rate +15, SpO₂ −3). False = absolute threshold.",
    )
    skip_when: list[Activity] = Field(
        default_factory=list,
        description="Rule is skipped during these activities; e.g. ['exercise'] for heart-rate deviation.",
    )
    source: str = Field(description="Source of the threshold (NASA-STD-3001 etc.) or 'HYPOTHESIS'.")


class Alert(_Model):
    id: str
    crew_id: str | None = Field(description="Null for a cabin alert.")
    metric: Metric
    severity: Severity
    message: str
    rule_id: str
    consecutive_count: int = Field(ge=1)
    first_seen: datetime
    last_seen: datetime
    why: list[Evidence] = Field(min_length=1, description="At least one piece of evidence is required.")

    @model_validator(mode="after")
    def _time_order(self) -> Alert:
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen cannot be earlier than first_seen")
        return self


class CommsMessage(_Model):
    id: str
    sender: Sender
    body: str
    sent_at: datetime
    deliver_at: datetime = Field(description="sent_at + one-way light delay.")
    one_way_delay_s: float = Field(ge=0)


class TelemetryFrame(_Model):
    """Full packet sent over SSE on every tick (event: telemetry)."""

    contract_version: str = CONTRACT_VERSION
    tick: int = Field(ge=0)
    ts: datetime
    mission_elapsed_s: float = Field(ge=0)
    scenario: Scenario
    simulated: bool = Field(default=True, description="Drives the 'SIMULATED' label on screen.")
    crew: list[CrewMember] = Field(min_length=CREW_SIZE, max_length=CREW_SIZE)
    cabin: CabinAtmosphere
    alerts: list[Alert] = Field(default_factory=list)
    comms_one_way_delay_s: float = Field(ge=0)
    messages: list[CommsMessage] = Field(default_factory=list)


CONTRACT_MODELS: list[type[BaseModel]] = [TelemetryFrame, Alert, ThresholdRule, CommsMessage]
CONTRACT_PATH = Path(__file__).resolve().parent.parent / "docs" / "contract.json"


def build_contract() -> dict:
    defs: dict = {}
    roots: dict = {}
    for model in CONTRACT_MODELS:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(schema.pop("$defs", {}))
        defs[model.__name__] = schema
        roots[model.__name__] = {"$ref": f"#/$defs/{model.__name__}"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Mars Transit Crew Health & Cabin Air Monitor - API contract",
        "contract_version": CONTRACT_VERSION,
        "generated_from": "backend/models.py",
        "crew_size": CREW_SIZE,
        "roots": roots,
        "$defs": dict(sorted(defs.items())),
    }


def render_contract() -> str:
    return json.dumps(build_contract(), indent=2, ensure_ascii=False) + "\n"


def write_contract(path: Path = CONTRACT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_contract(), encoding="utf-8", newline="\n")
    return path


if __name__ == "__main__":
    print(f"written: {write_contract()}")
