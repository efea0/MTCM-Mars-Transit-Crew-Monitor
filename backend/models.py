"""Tek doğruluk kaynağı: tüm API sözleşmesi bu Pydantic v2 modellerinden türetilir.

Değiştirdikten sonra sözleşmeyi yeniden üret:
    python -m backend.models
Sözleşme değişikliği Barış + Efe ortak kararıdır.

Temel ilkeler:
- Eksik veri asla "nominal" sayılmaz: value=None olan okuma quality != ok taşır,
  durumu unknown olur.
- Her uyarı (Alert) en az bir kanıt (Evidence) taşır ve art arda ölçümle tetiklenir.
- Sayısal eşikler burada değil, rules.py içindedir. Burada yalnızca yapı vardır.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "0.1.0"
CREW_SIZE = 4


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# ---------------------------------------------------------------- enumlar


class Status(str, Enum):
    """Bir ölçümün, kişinin veya kabinin genel durumu."""

    nominal = "nominal"
    caution = "caution"
    warning = "warning"
    critical = "critical"
    unknown = "unknown"  # eksik/bozuk veri; asla nominal yerine geçmez


class Severity(str, Enum):
    caution = "caution"
    warning = "warning"
    critical = "critical"


class Quality(str, Enum):
    ok = "ok"
    stale = "stale"  # son değer eski
    missing = "missing"  # veri hiç gelmedi
    sensor_fault = "sensor_fault"  # sensör hata bildirdi / fiziksel olarak imkânsız değer


class Comparator(str, Enum):
    lt = "<"
    le = "<="
    gt = ">"
    ge = ">="
    missing = "missing"  # eksik-veri kuralı


class Metric(str, Enum):
    # mürettebat
    heart_rate = "heart_rate"  # bpm
    hrv_rmssd = "hrv_rmssd"  # ms
    spo2 = "spo2"  # %
    resp_rate = "resp_rate"  # nefes/dk
    core_temp = "core_temp"  # °C
    systolic_bp = "systolic_bp"  # mmHg
    diastolic_bp = "diastolic_bp"  # mmHg
    # kabin
    pp_co2 = "pp_co2"  # mmHg
    pp_o2 = "pp_o2"  # kPa
    cabin_pressure = "cabin_pressure"  # kPa
    cabin_temp = "cabin_temp"  # °C
    rel_humidity = "rel_humidity"  # %
    radiation_dose_rate = "radiation_dose_rate"  # µGy/saat


class Scenario(str, Enum):
    nominal = "nominal"
    hypoxia = "hypoxia"  # O₂ düşüşü
    hypercapnia = "hypercapnia"  # CO₂ yükselişi / scrubber arızası
    sleep_deprivation = "sleep_deprivation"
    solar_storm = "solar_storm"  # radyasyon artışı (DONKI ile bağlanabilir)
    sensor_dropout = "sensor_dropout"  # eksik veri davranışını göstermek için


class Sender(str, Enum):
    crew = "crew"
    mission_control = "mission_control"
    system = "system"


# ---------------------------------------------------------------- ölçüm


class Reading(_Model):
    """Tek bir sensör okuması. value=None ise quality 'ok' olamaz."""

    value: float | None = Field(description="Ölçülen değer; veri yoksa null.")
    unit: str = Field(description="Örn. 'bpm', '%', 'mmHg', 'kPa'.")
    ts: datetime = Field(description="Ölçüm zamanı (UTC, ISO-8601).")
    quality: Quality = Quality.ok
    status: Status = Field(
        default=Status.unknown,
        description="rules.py doldurur; değerlendirilmemişse unknown.",
    )

    @model_validator(mode="after")
    def _missing_is_never_normal(self) -> "Reading":
        if self.value is None:
            if self.quality == Quality.ok.value:
                raise ValueError("value=None iken quality 'ok' olamaz")
            if self.status != Status.unknown.value:
                raise ValueError("value=None iken status yalnızca 'unknown' olabilir")
        elif self.quality != Quality.ok.value and self.status == Status.nominal.value:
            raise ValueError("kalitesi 'ok' olmayan okuma 'nominal' sayılamaz")
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


class CrewMember(_Model):
    id: str = Field(pattern=r"^crew-[1-9]$", description="Örn. 'crew-1'.")
    name: str
    role: str = Field(description="Örn. 'Commander', 'Flight Surgeon'.")
    vitals: CrewVitals
    status: Status = Status.unknown


# ---------------------------------------------------------------- uyarı


class Evidence(_Model):
    """Bir uyarının 'neden?' listesindeki tek kanıt maddesi."""

    metric: Metric
    value: float | None
    threshold: float | None = Field(description="Aşılan sınır; eksik-veri kanıtında null.")
    comparator: Comparator
    ts: datetime
    note: str = Field(description="İnsan-okunur açıklama, örn. 'SpO2 3 ölçümdür eşiğin altında'.")


class ThresholdRule(_Model):
    """Kural yapısı. Sayısal değerler ve kaynakları rules.py içinde tanımlanır."""

    id: str
    metric: Metric
    severity: Severity
    comparator: Comparator
    threshold: float | None = Field(description="comparator=missing ise null.")
    consecutive_required: int = Field(ge=1, description="Uyarı için art arda kaç ölçüm.")
    source: str = Field(description="Eşiğin kaynağı (NASA-STD-3001 vb.) veya 'HİPOTEZ'.")


class Alert(_Model):
    id: str
    crew_id: str | None = Field(description="Kabin uyarısında null.")
    metric: Metric
    severity: Severity
    message: str
    rule_id: str
    consecutive_count: int = Field(ge=1)
    first_seen: datetime
    last_seen: datetime
    why: list[Evidence] = Field(min_length=1, description="En az bir kanıt zorunlu.")

    @model_validator(mode="after")
    def _time_order(self) -> "Alert":
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen, first_seen'den önce olamaz")
        return self


# ---------------------------------------------------------------- iletişim


class CommsMessage(_Model):
    id: str
    sender: Sender
    body: str
    sent_at: datetime
    deliver_at: datetime = Field(description="sent_at + tek yön ışık gecikmesi.")
    one_way_delay_s: float = Field(ge=0)


# ---------------------------------------------------------------- SSE paketi


class TelemetryFrame(_Model):
    """SSE ile her tick'te gönderilen tam paket (event: telemetry)."""

    contract_version: str = CONTRACT_VERSION
    tick: int = Field(ge=0)
    ts: datetime
    mission_elapsed_s: float = Field(ge=0)
    scenario: Scenario
    simulated: bool = Field(default=True, description="Ekranda 'SİMÜLE' etiketi için.")
    crew: list[CrewMember] = Field(min_length=CREW_SIZE, max_length=CREW_SIZE)
    cabin: CabinAtmosphere
    alerts: list[Alert] = Field(default_factory=list)
    comms_one_way_delay_s: float = Field(ge=0)
    messages: list[CommsMessage] = Field(default_factory=list)


# ---------------------------------------------------------------- sözleşme üretimi

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
    print(f"yazıldı: {write_contract()}")
