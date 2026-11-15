"""Read-only reference payloads: displayed limits and the recorded DONKI SEP event."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final

STATUS_MAP: Final[dict[str, str]] = {
    "doğrulandı": "verified",
    "kısmen": "partial",
    "doğrulanmadı": "unverified",
}
STATUS_FALLBACK: Final[str] = "unverified"
SOURCE_FALLBACK: Final[str] = "assumption (team decision)"
SOURCE_REPLACEMENTS: Final[tuple[tuple[str, str], ...]] = (
    ("Tablo", "Table"),
    ("Bölüm", "Section"),
    ("varsayım", "assumption"),
    ("Ekip kararı", "team decision"),
)

CABIN_RULE_LABELS: Final[dict[str, str]] = {
    "pio2-normoxia-low": "Inspired O2 pressure below normoxia range",
    "pio2-hypoxia-low": "Inspired O2 pressure below mild hypoxia limit",
    "pio2-normoxia-high": "Inspired O2 pressure above normoxia range",
    "ppco2-limit": "Cabin CO2 partial pressure above limit",
    "ppco2-caution": "Cabin CO2 partial pressure above caution level",
}
CABIN_METRIC_UNITS: Final[dict[str, str]] = {"pio2": "mmHg", "pp_co2": "mmHg"}

# (ranges.json key, id prefix, label, unit, sign applied to the threshold, comparator)
VITAL_SPECS: Final[tuple[tuple[str, str, str, str, int, str], ...]] = (
    ("spo2_dusus", "spo2-drop", "SpO2 drop from personal baseline", "% points", -1, "<="),
    ("nabiz_artis_dinlenme", "hr-rise", "Resting heart rate rise from baseline", "bpm", 1, ">="),
    ("solunum_artis", "rr-rise", "Respiratory rate rise from baseline", "breaths/min", 1, ">="),
    ("isi_artis", "temp-rise", "Body temperature rise from baseline", "°C", 1, ">="),
)
# (ranges.json key, severity, label suffix)
VITAL_LEVELS: Final[tuple[tuple[str, str, str], ...]] = (
    ("izle", "caution", "watch"),
    ("eylem", "critical", "action"),
)
RADIATION_UNIT: Final[str] = "mSv"
RADIATION_LIMITS: Final[tuple[tuple[str, str, str], ...]] = (
    ("spe_tasarim_esigi", "radiation-spe", "Solar particle event dose"),
    ("kariyer_limiti", "radiation-career", "Career effective dose"),
)
THRESHOLD_DIGITS: Final[int] = 6

SEP_NOTE: Final[str] = (
    "Event time and type come from a real NASA DONKI record; dose values in this app are simulated."
)
ACTIVITY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})-([A-Z]+)-\d+$"
)


def normalize_source(raw: object) -> str:
    text = str(raw) if raw else SOURCE_FALLBACK
    for turkish, english in SOURCE_REPLACEMENTS:
        text = text.replace(turkish, english)
    return text


def map_status(raw: object) -> str:
    return STATUS_MAP.get(str(raw), STATUS_FALLBACK)


def _item(
    item_id: str,
    group: str,
    label: str,
    *,
    metric: str | None,
    severity: str | None,
    comparator: str | None,
    threshold: float | None,
    unit: str | None,
    consecutive_required: int | None,
    source: object,
    status: object,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "group": group,
        "label": label,
        "metric": metric,
        "severity": severity,
        "comparator": comparator,
        "threshold": threshold,
        "unit": unit,
        "consecutive_required": consecutive_required,
        "source": normalize_source(source),
        "status": map_status(status),
    }


def _cabin_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for rule in data.get("kurallar", []):
        try:
            rule_id = str(rule["id"])
            metric = str(rule["metric"])
            items.append(
                _item(
                    rule_id,
                    "cabin",
                    CABIN_RULE_LABELS.get(rule_id, rule_id),
                    metric=metric,
                    severity=str(rule["severity"]),
                    comparator=str(rule["comparator"]),
                    threshold=rule["threshold"],
                    unit=CABIN_METRIC_UNITS.get(metric),
                    consecutive_required=int(rule["consecutive_required"]),
                    source=rule.get("source"),
                    status=rule.get("status"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return items


def _vital_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    vitals = data.get("vital_karar_parametreleri", {})
    consecutive: int | None
    try:
        consecutive = int(vitals["art_arda_olcum"]["deger"])
    except (KeyError, TypeError, ValueError):
        consecutive = None
    items: list[dict[str, Any]] = []
    for key, prefix, label, unit, sign, comparator in VITAL_SPECS:
        params = vitals.get(key)
        if not isinstance(params, dict):
            continue
        for level_key, severity, level_name in VITAL_LEVELS:
            try:
                threshold = sign * params[level_key]
            except (KeyError, TypeError):
                continue
            items.append(
                _item(
                    f"{prefix}-{level_name}",
                    "vitals",
                    f"{label} ({level_name})",
                    metric=None,
                    severity=severity,
                    comparator=comparator,
                    threshold=threshold,
                    unit=unit,
                    consecutive_required=consecutive,
                    source=params.get("kaynak"),
                    status=params.get("status"),
                )
            )
    return items


def _radiation_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    radiation = data.get("radyasyon", {})
    yellow = data.get("vital_karar_parametreleri", {}).get("sari_kademe_genel", {})
    ratio = yellow.get("oran")
    items: list[dict[str, Any]] = []
    for key, prefix, label in RADIATION_LIMITS:
        entry = radiation.get(key)
        if not isinstance(entry, dict) or "deger" not in entry:
            continue
        limit = entry["deger"]
        items.append(
            _item(
                f"{prefix}-limit",
                "radiation",
                f"{label} limit",
                metric=None,
                severity="critical",
                comparator=">=",
                threshold=limit,
                unit=RADIATION_UNIT,
                consecutive_required=None,
                source=entry.get("kaynak"),
                status=entry.get("status"),
            )
        )
        if isinstance(ratio, int | float):
            items.append(
                _item(
                    f"{prefix}-watch",
                    "radiation",
                    f"{label} watch level",
                    metric=None,
                    severity="caution",
                    comparator=">=",
                    threshold=round(ratio * limit, THRESHOLD_DIGITS),
                    unit=RADIATION_UNIT,
                    consecutive_required=None,
                    source=yellow.get("kaynak"),
                    status=yellow.get("status"),
                )
            )
    return items


def build_limits(ranges_path: Path) -> dict[str, Any]:
    data = json.loads(ranges_path.read_text(encoding="utf-8"))
    version = data.get("meta", {}).get("surum")
    items = _cabin_items(data) + _vital_items(data) + _radiation_items(data)
    return {"version": str(version) if version is not None else None, "items": items}


def _linked_event(activity_id: object) -> dict[str, str] | None:
    match = ACTIVITY_ID_PATTERN.match(str(activity_id))
    if match is None:
        return None
    return {"activity_id": str(activity_id), "type": match.group(2), "time": f"{match.group(1)}Z"}


def build_sep_event(path: Path) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    selected = record["selected"]
    linked = [
        event
        for entry in selected.get("linkedEvents") or []
        if (event := _linked_event(entry.get("activityID"))) is not None
    ]
    linked.sort(key=lambda event: (event["time"], event["activity_id"]))
    return {
        "sep_id": selected["sepID"],
        "event_time": selected["eventTime"],
        "instruments": [i["displayName"] for i in selected.get("instruments") or [] if "displayName" in i],
        "link": selected.get("link"),
        "source": record.get("source"),
        "fetched_at": record.get("fetched_at"),
        "linked_events": linked,
        "note": SEP_NOTE,
    }
