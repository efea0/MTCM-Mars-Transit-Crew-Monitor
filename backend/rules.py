"""Rules engine: evaluates a telemetry frame and produces alerts and statuses.

Based on NASA-STD-3001 and data/reference/ranges.json, it:
1. Applies personal baselines and fills Reading.baseline_delta.
2. Derives PIO2 from cabin pp_o2 and cabin pressure (kPa/mmHg unit conversion).
3. Applies the activity gate (skip_when) to suppress misleading vital alerts
   during exercise, sleep or unknown activity.
4. Resets the counter on missing/faulty data and raises a missing alert after
   consecutive gaps.
5. Enforces the consecutive-readings rule (consecutive_required).
6. Produces per-crew decisions (hypoxia, CO2 effect, temperature deviation,
   cardiac load, radiation effect).
7. Computes the 1-hour rolling average window for ppCO2 (average_window_s).
8. Determines overall crew and cabin status and the crew decision.
9. Reads all medical and environmental numeric thresholds from ranges.json at
   runtime; only the unit conversion (MMHG_PER_KPA), missing-data threshold
   (MISSING_ALERT_AFTER) and window length (CO2_LIMIT_WINDOW_S) are named
   constants at module level.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from backend.models import (
    Activity,
    Alert,
    CabinAtmosphere,
    Comparator,
    CrewDecision,
    CrewMember,
    CrewVitals,
    Evidence,
    Metric,
    Quality,
    Reading,
    Severity,
    Status,
    TelemetryFrame,
    ThresholdRule,
)

DEFAULT_RANGES: Final[Path] = Path(__file__).resolve().parent.parent / "data" / "reference" / "ranges.json"
MISSING_ALERT_AFTER: Final[int] = 3
CO2_LIMIT_WINDOW_S: Final[float] = 3600.0
MMHG_PER_KPA: Final[float] = 7.50062

SEVERITY_BY_LEVEL: Final[dict[str, Severity]] = {
    "izle": Severity.caution,
    "eylem": Severity.critical,
}

STATUS_BY_SEVERITY: Final[dict[Severity, Status]] = {
    Severity.critical: Status.critical,
    Severity.warning: Status.warning,
    Severity.caution: Status.caution,
}

DECISION_BY_STATUS: Final[dict[Status, CrewDecision]] = {
    Status.critical: CrewDecision.action,
    Status.warning: CrewDecision.action,
    Status.caution: CrewDecision.watch,
    Status.nominal: CrewDecision.normal,
    Status.unknown: CrewDecision.unknown,
}


@dataclass(frozen=True)
class SepContext:
    sep_active: bool
    event_dose_rate_msv_per_h: float | None = None
    remaining_event_h: float | None = None


def derive_pio2(pp_o2: Reading, cabin_pressure: Reading, water_vapour_mmhg: float) -> float | None:
    if pp_o2.value is None or pp_o2.quality != Quality.ok.value:
        return None
    if cabin_pressure.value is None or cabin_pressure.quality != Quality.ok.value:
        return None

    pb_val = cabin_pressure.value
    pb_mmhg = pb_val * MMHG_PER_KPA if cabin_pressure.unit.lower() == "kpa" else pb_val
    if pb_mmhg <= water_vapour_mmhg:
        return None

    o2_val = pp_o2.value
    pp_o2_mmhg = o2_val * MMHG_PER_KPA if pp_o2.unit.lower() == "kpa" else o2_val
    if pp_o2_mmhg <= 0:
        return 0.0

    fio2 = pp_o2_mmhg / pb_mmhg
    return (pb_mmhg - water_vapour_mmhg) * fio2


class RulesEngine:
    def __init__(
        self,
        ranges_path: str | Path = DEFAULT_RANGES,
        *,
        profile: str = "mars_transit",
        co2_window_s: float | None = CO2_LIMIT_WINDOW_S,
    ) -> None:
        self.ranges_path = Path(ranges_path)
        self.profile_name = profile
        self.co2_window_s = co2_window_s

        self._load_reference_data()

        self._co2_window: list[tuple[float, float]] = []  # (mission_elapsed_s, pp_co2_mmhg)
        self._missing_counters: dict[tuple[str, Metric], int] = {}
        self._missing_first_seen: dict[tuple[str, Metric], datetime] = {}
        self._missing_alerts: dict[tuple[str, Metric], Alert] = {}

        self._consecutive_counts: dict[str, int] = {}
        self._alert_first_seen: dict[str, datetime] = {}
        self._active_alerts: dict[str, Alert] = {}

    def _load_reference_data(self) -> None:
        with open(self.ranges_path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)

        pio2_formul = data["kabin"]["pio2_formulu"]["formul"]
        matches = re.findall(r"\bPB\s*[-−]\s*(\d+(?:[.,]\d+)?)", pio2_formul)
        if len(matches) != 1:
            raise ValueError(f"ranges: could not parse water vapour value in pio2_formulu: {pio2_formul}")
        self.water_vapour_mmhg = float(matches[0].replace(",", "."))

        vital_karar = data["vital_karar_parametreleri"]
        self.consecutive_required = int(vital_karar["art_arda_olcum"]["deger"])
        self.sari_oran = float(vital_karar["sari_kademe_genel"]["oran"])

        self.baselines: dict[str, dict[Metric, float]] = {}
        for person in vital_karar["baz_cizgi"]["astronotlar"]:
            cid = f"crew-{person['id']}"
            self.baselines[cid] = {
                Metric.heart_rate: float(person["nabiz"]),
                Metric.spo2: float(person["spo2"]),
                Metric.core_temp: float(person["isi"]),
                Metric.resp_rate: float(person["solunum"]),
            }

        self.spo2_izle = float(vital_karar["spo2_dusus"]["izle"])
        self.spo2_eylem = float(vital_karar["spo2_dusus"]["eylem"])
        self.hr_izle = float(vital_karar["nabiz_artis_dinlenme"]["izle"])
        self.hr_eylem = float(vital_karar["nabiz_artis_dinlenme"]["eylem"])
        self.rr_izle = float(vital_karar["solunum_artis"]["izle"])
        self.rr_eylem = float(vital_karar["solunum_artis"]["eylem"])
        self.temp_izle = float(vital_karar["isi_artis"]["izle"])
        self.temp_eylem = float(vital_karar["isi_artis"]["eylem"])

        kabin = data["kabin"]
        self.ppco2_limit_1saat_ort = float(kabin["ppco2_limit_1saat_ort"]["deger"])
        self.ppco2_sari_esik = float(kabin["ppco2_sari_esik"]["deger"])
        self.pio2_normoksi_min = float(kabin["pio2_normoksi_hedef"]["min"])
        self.pio2_normoksi_max = float(kabin["pio2_normoksi_hedef"]["max"])
        self.pio2_hafif_hipoksi_alt = float(kabin["pio2_hafif_hipoksi_alt"]["deger"])
        self.pio2_mutlak_alt = float(kabin["pio2_mutlak_alt"]["deger"])
        self.pressure_min = float(kabin["toplam_basinc_suresiz"]["min_haric"])
        self.pressure_max = float(kabin["toplam_basinc_suresiz"]["max"])

        radyasyon = data["radyasyon"]
        self.kariyer_limiti = float(radyasyon["kariyer_limiti"]["deger"])
        self.spe_tasarim_esigi = float(radyasyon["spe_tasarim_esigi"]["deger"])

        self.rule_validation_status: dict[str, str] = {}
        cabin_rules_list: list[ThresholdRule] = []
        for row in data.get("kurallar", []):
            rule_id = str(row["id"])
            self.rule_validation_status[rule_id] = str(row.get("status", "doğrulanmadı"))
            window = self.co2_window_s if rule_id == "ppco2-limit" else None
            rule = ThresholdRule(
                id=rule_id,
                metric=Metric(str(row["metric"])),
                severity=Severity(str(row["severity"])),
                comparator=Comparator(str(row["comparator"])),
                threshold=float(row["threshold"]),
                consecutive_required=int(row["consecutive_required"]),
                average_window_s=window,
                relative_to_baseline=False,
                skip_when=[],
                source=str(row.get("source", "NASA-STD-3001")),
            )
            cabin_rules_list.append(rule)
        self.cabin_rules = tuple(cabin_rules_list)

        source = "varsayım (Ekip kararı)"
        skip_non_resting = [Activity.exercise, Activity.sleep, Activity.unknown]
        vital_rules_list = [
            ThresholdRule(
                id="spo2-drop-caution",
                metric=Metric.spo2,
                severity=Severity.caution,
                comparator=Comparator.le,
                threshold=-self.spo2_izle,
                consecutive_required=self.consecutive_required,
                relative_to_baseline=True,
                skip_when=[],
                source=source,
            ),
            ThresholdRule(
                id="spo2-drop-critical",
                metric=Metric.spo2,
                severity=Severity.critical,
                comparator=Comparator.le,
                threshold=-self.spo2_eylem,
                consecutive_required=self.consecutive_required,
                relative_to_baseline=True,
                skip_when=[],
                source=source,
            ),
            ThresholdRule(
                id="hr-rest-rise-caution",
                metric=Metric.heart_rate,
                severity=Severity.caution,
                comparator=Comparator.ge,
                threshold=self.hr_izle,
                consecutive_required=self.consecutive_required,
                relative_to_baseline=True,
                skip_when=skip_non_resting,
                source=source,
            ),
            ThresholdRule(
                id="hr-rest-rise-critical",
                metric=Metric.heart_rate,
                severity=Severity.critical,
                comparator=Comparator.ge,
                threshold=self.hr_eylem,
                consecutive_required=self.consecutive_required,
                relative_to_baseline=True,
                skip_when=skip_non_resting,
                source=source,
            ),
            ThresholdRule(
                id="rr-rise-caution",
                metric=Metric.resp_rate,
                severity=Severity.caution,
                comparator=Comparator.ge,
                threshold=self.rr_izle,
                consecutive_required=self.consecutive_required,
                relative_to_baseline=True,
                skip_when=skip_non_resting,
                source=source,
            ),
            ThresholdRule(
                id="rr-rise-critical",
                metric=Metric.resp_rate,
                severity=Severity.critical,
                comparator=Comparator.ge,
                threshold=self.rr_eylem,
                consecutive_required=self.consecutive_required,
                relative_to_baseline=True,
                skip_when=skip_non_resting,
                source=source,
            ),
            ThresholdRule(
                id="temp-rise-caution",
                metric=Metric.core_temp,
                severity=Severity.caution,
                comparator=Comparator.ge,
                threshold=self.temp_izle,
                consecutive_required=self.consecutive_required,
                relative_to_baseline=True,
                skip_when=[],
                source=source,
            ),
            ThresholdRule(
                id="temp-rise-critical",
                metric=Metric.core_temp,
                severity=Severity.critical,
                comparator=Comparator.ge,
                threshold=self.temp_eylem,
                consecutive_required=self.consecutive_required,
                relative_to_baseline=True,
                skip_when=[],
                source=source,
            ),
        ]
        self.vital_rules = tuple(vital_rules_list)
        self.rules = tuple(self.cabin_rules) + tuple(self.vital_rules)

    def reset(self) -> None:
        self._co2_window.clear()
        self._missing_counters.clear()
        self._missing_first_seen.clear()
        self._missing_alerts.clear()
        self._consecutive_counts.clear()
        self._alert_first_seen.clear()
        self._active_alerts.clear()

    def evaluate(self, frame: TelemetryFrame, *, sep: SepContext | None = None) -> TelemetryFrame:
        cabin = frame.cabin
        pio2_mmhg = derive_pio2(cabin.pp_o2, cabin.cabin_pressure, self.water_vapour_mmhg)

        self._update_missing_reading("cabin", Metric.pp_co2, cabin.pp_co2, frame.ts)
        self._update_missing_reading("cabin", Metric.pp_o2, cabin.pp_o2, frame.ts)
        self._update_missing_reading("cabin", Metric.cabin_pressure, cabin.cabin_pressure, frame.ts)
        self._update_missing_reading("cabin", Metric.cabin_temp, cabin.cabin_temp, frame.ts)
        self._update_missing_reading("cabin", Metric.rel_humidity, cabin.rel_humidity, frame.ts)
        self._update_missing_reading("cabin", Metric.radiation_dose_rate, cabin.radiation_dose_rate, frame.ts)

        self._evaluate_cabin_rules(frame, pio2_mmhg)

        updated_crew: list[CrewMember] = []
        for crew in frame.crew:
            member = self._evaluate_crew_member(crew, frame, pio2_mmhg, sep)
            updated_crew.append(member)

        updated_cabin = self._finalize_cabin(cabin, pio2_mmhg)

        all_alerts = list(self._active_alerts.values()) + list(self._missing_alerts.values())
        all_alerts.sort(key=lambda a: a.id)

        return frame.model_copy(
            update={
                "crew": updated_crew,
                "cabin": updated_cabin,
                "alerts": all_alerts,
            }
        )

    def _update_missing_reading(
        self, subject_id: str, metric: Metric, reading: Reading, ts: datetime
    ) -> None:
        key = (subject_id, metric)
        is_ok = reading.value is not None and reading.quality == Quality.ok.value
        if not is_ok:
            count = self._missing_counters.get(key, 0) + 1
            self._missing_counters[key] = count
            if count == 1:
                self._missing_first_seen[key] = reading.ts
            if count >= MISSING_ALERT_AFTER:
                first_seen = self._missing_first_seen[key]
                alert_id = f"{subject_id}:missing-{metric.value}"
                rule_id = f"missing-{metric.value}"
                name = subject_id if subject_id != "cabin" else "Cabin"
                alert = Alert(
                    id=alert_id,
                    crew_id=subject_id if subject_id != "cabin" else None,
                    metric=metric,
                    severity=Severity.caution,
                    message=f"{name}: no data from {metric.value} sensor",
                    rule_id=rule_id,
                    consecutive_count=count,
                    first_seen=first_seen,
                    last_seen=ts,
                    why=[
                        Evidence(
                            metric=metric,
                            value=None,
                            threshold=None,
                            comparator=Comparator.missing,
                            ts=reading.ts,
                            note=f"no data for {count} readings",
                        )
                    ],
                )
                self._missing_alerts[key] = alert
        else:
            self._missing_counters[key] = 0
            self._missing_first_seen.pop(key, None)
            self._missing_alerts.pop(key, None)

    def _evaluate_cabin_rules(self, frame: TelemetryFrame, pio2_mmhg: float | None) -> None:
        cabin = frame.cabin
        co2_val = cabin.pp_co2.value
        co2_ok = co2_val is not None and cabin.pp_co2.quality == Quality.ok.value

        if co2_ok:
            assert co2_val is not None
            co2_mmhg = co2_val * MMHG_PER_KPA if cabin.pp_co2.unit.lower() == "kpa" else co2_val
            if self.co2_window_s is not None:
                self._co2_window.append((frame.mission_elapsed_s, co2_mmhg))
                cutoff = frame.mission_elapsed_s - self.co2_window_s
                self._co2_window = [(t, v) for t, v in self._co2_window if t >= cutoff]
                samples = [v for _, v in self._co2_window]
                co2_avg = sum(samples) / len(samples)
                window_full = (frame.mission_elapsed_s - self._co2_window[0][0]) >= self.co2_window_s
            else:
                samples = [co2_mmhg]
                co2_avg = co2_mmhg
                window_full = True
        else:
            co2_mmhg = None
            co2_avg = None
            samples = []
            window_full = False

        # ppco2-caution
        caution_id = "cabin:ppco2-caution"
        caution_cond = co2_ok and co2_mmhg is not None and co2_mmhg > self.ppco2_sari_esik
        if caution_cond:
            assert co2_mmhg is not None
            count = self._consecutive_counts.get(caution_id, 0) + 1
            self._consecutive_counts[caution_id] = count
            if count == 1:
                self._alert_first_seen[caution_id] = frame.ts
            if count >= self.consecutive_required:
                note = (
                    f"Cabin ppCO2 {co2_mmhg:.2f} mmHg (threshold {self.ppco2_sari_esik} mmHg), "
                    f"for {count} readings"
                )
                why = [
                    Evidence(
                        metric=Metric.pp_co2,
                        value=round(co2_mmhg, 2),
                        threshold=self.ppco2_sari_esik,
                        comparator=Comparator.gt,
                        ts=cabin.pp_co2.ts,
                        note=note,
                    )
                ]
                msg = f"Cabin ppCO2 above caution threshold: {co2_mmhg:.2f} mmHg. Check CO2 scrubbing."
                self._active_alerts[caution_id] = Alert(
                    id=caution_id,
                    crew_id=None,
                    metric=Metric.pp_co2,
                    severity=Severity.caution,
                    message=msg,
                    rule_id="ppco2-caution",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[caution_id],
                    last_seen=frame.ts,
                    why=why,
                )
        else:
            self._consecutive_counts[caution_id] = 0
            self._alert_first_seen.pop(caution_id, None)
            self._active_alerts.pop(caution_id, None)

        # ppco2-limit
        limit_id = "cabin:ppco2-limit"
        limit_cond = co2_ok and co2_avg is not None and co2_avg > self.ppco2_limit_1saat_ort
        if limit_cond:
            assert co2_avg is not None
            count = self._consecutive_counts.get(limit_id, 0) + 1
            self._consecutive_counts[limit_id] = count
            if count == 1:
                self._alert_first_seen[limit_id] = frame.ts
            if count >= self.consecutive_required:
                if self.co2_window_s is not None and not window_full:
                    note = (
                        f"1-hour window not yet full (average of {len(samples)} samples "
                        f"{co2_avg:.2f} mmHg, threshold {self.ppco2_limit_1saat_ort} mmHg), "
                        f"for {count} readings"
                    )
                elif self.co2_window_s is not None:
                    note = (
                        f"1-hour average ppCO2 {co2_avg:.2f} mmHg (threshold "
                        f"{self.ppco2_limit_1saat_ort} mmHg), for {count} readings"
                    )
                else:
                    note = (
                        f"Cabin ppCO2 {co2_avg:.2f} mmHg (threshold {self.ppco2_limit_1saat_ort} mmHg), "
                        f"for {count} readings"
                    )
                why = [
                    Evidence(
                        metric=Metric.pp_co2,
                        value=round(co2_avg, 3),
                        threshold=self.ppco2_limit_1saat_ort,
                        comparator=Comparator.gt,
                        ts=cabin.pp_co2.ts,
                        note=note,
                    )
                ]
                msg = (
                    f"Cabin ppCO2 above limit: {co2_avg:.2f} mmHg. "
                    "Engage backup CO2 scrubbing, reduce effort."
                )
                self._active_alerts[limit_id] = Alert(
                    id=limit_id,
                    crew_id=None,
                    metric=Metric.pp_co2,
                    severity=Severity.critical,
                    message=msg,
                    rule_id="ppco2-limit",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[limit_id],
                    last_seen=frame.ts,
                    why=why,
                )
        else:
            self._consecutive_counts[limit_id] = 0
            self._alert_first_seen.pop(limit_id, None)
            self._active_alerts.pop(limit_id, None)

        # pio2-hypoxia-low (< 127, critical)
        normoxia_low_id = "cabin:pio2-normoxia-low"
        hypoxia_low_id = "cabin:pio2-hypoxia-low"
        hypoxia_low_cond = pio2_mmhg is not None and pio2_mmhg < self.pio2_hafif_hipoksi_alt
        if hypoxia_low_cond:
            assert pio2_mmhg is not None
            count = self._consecutive_counts.get(hypoxia_low_id, 0) + 1
            self._consecutive_counts[hypoxia_low_id] = count
            if count == 1:
                self._alert_first_seen[hypoxia_low_id] = frame.ts
            if count >= self.consecutive_required:
                note = (
                    f"Cabin PIO2 {pio2_mmhg:.1f} mmHg (mild hypoxia lower bound "
                    f"{self.pio2_hafif_hipoksi_alt} mmHg), for {count} readings"
                )
                why = [
                    Evidence(
                        metric=Metric.pio2,
                        value=round(pio2_mmhg, 2),
                        threshold=self.pio2_hafif_hipoksi_alt,
                        comparator=Comparator.lt,
                        ts=frame.ts,
                        note=note,
                    )
                ]
                msg = (
                    f"Cabin O2 below mild hypoxia bound: PIO2 {pio2_mmhg:.1f} mmHg. "
                    "Switch to backup O2 and stop exercise."
                )
                self._active_alerts[hypoxia_low_id] = Alert(
                    id=hypoxia_low_id,
                    crew_id=None,
                    metric=Metric.pio2,
                    severity=Severity.critical,
                    message=msg,
                    rule_id="pio2-hypoxia-low",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[hypoxia_low_id],
                    last_seen=frame.ts,
                    why=why,
                )
                self._active_alerts.pop(normoxia_low_id, None)
        else:
            self._consecutive_counts[hypoxia_low_id] = 0
            self._alert_first_seen.pop(hypoxia_low_id, None)
            self._active_alerts.pop(hypoxia_low_id, None)

        # pio2-normoxia-low (< 145, caution)
        normoxia_low_id = "cabin:pio2-normoxia-low"
        normoxia_low_cond = pio2_mmhg is not None and pio2_mmhg < self.pio2_normoksi_min
        if normoxia_low_cond:
            assert pio2_mmhg is not None
            count = self._consecutive_counts.get(normoxia_low_id, 0) + 1
            self._consecutive_counts[normoxia_low_id] = count
            if count == 1:
                self._alert_first_seen[normoxia_low_id] = frame.ts
            if count >= self.consecutive_required:
                if hypoxia_low_id in self._active_alerts:
                    self._active_alerts.pop(normoxia_low_id, None)
                else:
                    note = (
                        f"Cabin PIO2 {pio2_mmhg:.1f} mmHg (normoxia lower bound "
                        f"{self.pio2_normoksi_min} mmHg), for {count} readings"
                    )
                    why = [
                        Evidence(
                            metric=Metric.pio2,
                            value=round(pio2_mmhg, 2),
                            threshold=self.pio2_normoksi_min,
                            comparator=Comparator.lt,
                            ts=frame.ts,
                            note=note,
                        )
                    ]
                    msg = f"Cabin O2 below normoxia range: PIO2 {pio2_mmhg:.1f} mmHg. " "Check O2 generation."
                    self._active_alerts[normoxia_low_id] = Alert(
                        id=normoxia_low_id,
                        crew_id=None,
                        metric=Metric.pio2,
                        severity=Severity.caution,
                        message=msg,
                        rule_id="pio2-normoxia-low",
                        consecutive_count=count,
                        first_seen=self._alert_first_seen[normoxia_low_id],
                        last_seen=frame.ts,
                        why=why,
                    )
        else:
            self._consecutive_counts[normoxia_low_id] = 0
            self._alert_first_seen.pop(normoxia_low_id, None)
            self._active_alerts.pop(normoxia_low_id, None)

        # pio2-normoxia-high (> 155, caution)
        normoxia_high_id = "cabin:pio2-normoxia-high"
        normoxia_high_cond = pio2_mmhg is not None and pio2_mmhg > self.pio2_normoksi_max
        if normoxia_high_cond:
            assert pio2_mmhg is not None
            count = self._consecutive_counts.get(normoxia_high_id, 0) + 1
            self._consecutive_counts[normoxia_high_id] = count
            if count == 1:
                self._alert_first_seen[normoxia_high_id] = frame.ts
            if count >= self.consecutive_required:
                note = (
                    f"Cabin PIO2 {pio2_mmhg:.1f} mmHg (normoxia upper bound "
                    f"{self.pio2_normoksi_max} mmHg), for {count} readings"
                )
                why = [
                    Evidence(
                        metric=Metric.pio2,
                        value=round(pio2_mmhg, 2),
                        threshold=self.pio2_normoksi_max,
                        comparator=Comparator.gt,
                        ts=frame.ts,
                        note=note,
                    )
                ]
                msg = f"Cabin O2 above normoxia range: PIO2 {pio2_mmhg:.1f} mmHg. " "Check O2 settings."
                self._active_alerts[normoxia_high_id] = Alert(
                    id=normoxia_high_id,
                    crew_id=None,
                    metric=Metric.pio2,
                    severity=Severity.caution,
                    message=msg,
                    rule_id="pio2-normoxia-high",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[normoxia_high_id],
                    last_seen=frame.ts,
                    why=why,
                )
        else:
            self._consecutive_counts[normoxia_high_id] = 0
            self._alert_first_seen.pop(normoxia_high_id, None)
            self._active_alerts.pop(normoxia_high_id, None)

    def _evaluate_crew_member(
        self,
        crew: CrewMember,
        frame: TelemetryFrame,
        pio2_mmhg: float | None,
        sep: SepContext | None,
    ) -> CrewMember:
        cid = crew.id
        if cid not in self.baselines:
            raise ValueError(f"no baseline for {cid}")
        baselines = self.baselines[cid]
        for m in (Metric.heart_rate, Metric.spo2, Metric.core_temp, Metric.resp_rate):
            if m not in baselines:
                raise ValueError(f"no {m.value} baseline for {cid}")

        for field_name in CrewVitals.model_fields:
            metric = Metric(field_name)
            reading = getattr(crew.vitals, field_name)
            self._update_missing_reading(cid, metric, reading, frame.ts)

        vitals_dict: dict[str, Reading] = {}
        for field_name in CrewVitals.model_fields:
            metric = Metric(field_name)
            r: Reading = getattr(crew.vitals, field_name)
            base = baselines.get(metric)

            is_valid = r.value is not None and r.quality == Quality.ok.value
            if not is_valid:
                delta = None
                r_status = Status.unknown
            else:
                assert r.value is not None
                delta = round(r.value - base, 6) if base is not None else None
                r_status = self._evaluate_vital_reading_status(metric, delta, crew.activity)

            vitals_dict[field_name] = r.model_copy(update={"baseline_delta": delta, "status": r_status})

        updated_vitals = CrewVitals(**vitals_dict)

        sep_missing = self._evaluate_crew_decisions(crew, updated_vitals, frame, pio2_mmhg, sep)

        all_active_crew_alerts = [
            a
            for a in list(self._active_alerts.values()) + list(self._missing_alerts.values())
            if a.crew_id == cid
        ]

        if all_active_crew_alerts:
            severities = {a.severity for a in all_active_crew_alerts}
            if Severity.critical.value in severities:
                c_status = Status.critical
                c_decision = CrewDecision.action
            elif Severity.warning.value in severities:
                c_status = Status.warning
                c_decision = CrewDecision.action
            else:
                c_status = Status.caution
                c_decision = CrewDecision.watch
        else:
            has_missing_vital = any(
                getattr(updated_vitals, f).value is None
                or getattr(updated_vitals, f).quality != Quality.ok.value
                for f in CrewVitals.model_fields
            )
            if has_missing_vital or sep_missing:
                c_status = Status.unknown
                c_decision = CrewDecision.unknown
            else:
                c_status = Status.nominal
                c_decision = CrewDecision.normal

        return crew.model_copy(
            update={
                "vitals": updated_vitals,
                "status": c_status,
                "decision": c_decision,
            }
        )

    def _evaluate_vital_reading_status(
        self, metric: Metric, delta: float | None, activity: Activity
    ) -> Status:
        if delta is None:
            return Status.nominal

        if metric == Metric.spo2:
            if delta <= -self.spo2_eylem:
                return Status.critical
            if delta <= -self.spo2_izle:
                return Status.caution
            return Status.nominal

        if metric == Metric.heart_rate:
            if activity != Activity.resting.value:
                return Status.nominal
            if delta >= self.hr_eylem:
                return Status.critical
            if delta >= self.hr_izle:
                return Status.caution
            return Status.nominal

        if metric == Metric.resp_rate:
            if activity != Activity.resting.value:
                return Status.nominal
            if delta >= self.rr_eylem:
                return Status.critical
            if delta >= self.rr_izle:
                return Status.caution
            return Status.nominal

        if metric == Metric.core_temp:
            if delta >= self.temp_eylem:
                return Status.critical
            if delta >= self.temp_izle:
                return Status.caution
            return Status.nominal

        return Status.nominal

    def _evaluate_crew_decisions(
        self,
        crew: CrewMember,
        vitals: CrewVitals,
        frame: TelemetryFrame,
        pio2_mmhg: float | None,
        sep: SepContext | None,
    ) -> bool:
        cid = crew.id
        if cid not in self.baselines:
            raise ValueError(f"no baseline for {cid}")
        baselines = self.baselines[cid]
        for m in (Metric.heart_rate, Metric.spo2, Metric.core_temp, Metric.resp_rate):
            if m not in baselines:
                raise ValueError(f"no {m.value} baseline for {cid}")

        hipoksi_id = f"{cid}:karar-olasi_hipoksi"
        spo2_r = vitals.spo2
        spo2_ok = (
            spo2_r.value is not None
            and spo2_r.quality == Quality.ok.value
            and spo2_r.baseline_delta is not None
        )
        pio2_ok = pio2_mmhg is not None

        if (
            spo2_ok
            and pio2_ok
            and spo2_r.baseline_delta is not None
            and pio2_mmhg is not None
            and spo2_r.baseline_delta <= -self.spo2_izle
            and pio2_mmhg < self.pio2_normoksi_min
        ):
            if crew.activity == Activity.resting.value:
                hr_r = vitals.heart_rate
                rr_r = vitals.resp_rate
                hr_ok = (
                    hr_r.value is not None
                    and hr_r.quality == Quality.ok.value
                    and hr_r.baseline_delta is not None
                    and hr_r.baseline_delta >= self.hr_izle
                )
                rr_ok = (
                    rr_r.value is not None
                    and rr_r.quality == Quality.ok.value
                    and rr_r.baseline_delta is not None
                    and rr_r.baseline_delta >= self.rr_izle
                )
                hipoksi_cond = hr_ok or rr_ok
            else:
                hipoksi_cond = True
                hr_ok = False
                rr_ok = False
        else:
            hipoksi_cond = False
            hr_ok = False
            rr_ok = False

        if hipoksi_cond:
            assert spo2_r.baseline_delta is not None
            assert pio2_mmhg is not None
            sev = (
                Severity.critical
                if (spo2_r.baseline_delta <= -self.spo2_eylem or pio2_mmhg < self.pio2_hafif_hipoksi_alt)
                else Severity.caution
            )
            count = self._consecutive_counts.get(hipoksi_id, 0) + 1
            self._consecutive_counts[hipoksi_id] = count
            if count == 1:
                self._alert_first_seen[hipoksi_id] = frame.ts
            if count >= self.consecutive_required:
                base_spo2 = baselines[Metric.spo2]
                thr_spo2 = base_spo2 - (self.spo2_eylem if sev == Severity.critical else self.spo2_izle)
                why = [
                    Evidence(
                        metric=Metric.spo2,
                        value=spo2_r.value,
                        threshold=round(thr_spo2, 1),
                        comparator=Comparator.le,
                        ts=spo2_r.ts,
                        note=(
                            f"SpO2 {spo2_r.baseline_delta:+.1f} points from baseline "
                            f"(threshold -{self.spo2_izle:.1f}), for {count} readings"
                        ),
                    ),
                    Evidence(
                        metric=Metric.pio2,
                        value=round(pio2_mmhg, 1),
                        threshold=self.pio2_normoksi_min,
                        comparator=Comparator.lt,
                        ts=frame.ts,
                        note=(
                            f"Cabin PIO2 {pio2_mmhg:.1f} mmHg (normoxia lower bound "
                            f"{self.pio2_normoksi_min} mmHg), for {count} readings"
                        ),
                    ),
                ]
                if crew.activity == Activity.resting.value:
                    if hr_ok:
                        hr_r = vitals.heart_rate
                        assert hr_r.baseline_delta is not None
                        why.append(
                            Evidence(
                                metric=Metric.heart_rate,
                                value=hr_r.value,
                                threshold=round(baselines[Metric.heart_rate] + self.hr_izle, 1),
                                comparator=Comparator.ge,
                                ts=hr_r.ts,
                                note=(
                                    f"Resting heart rate {hr_r.baseline_delta:+.1f} bpm from baseline, "
                                    f"for {count} readings"
                                ),
                            )
                        )
                    if rr_ok:
                        rr_r = vitals.resp_rate
                        assert rr_r.baseline_delta is not None
                        why.append(
                            Evidence(
                                metric=Metric.resp_rate,
                                value=rr_r.value,
                                threshold=round(baselines[Metric.resp_rate] + self.rr_izle, 1),
                                comparator=Comparator.ge,
                                ts=rr_r.ts,
                                note=(
                                    f"Respiration rate {rr_r.baseline_delta:+.1f} breaths/min from baseline, "
                                    f"for {count} readings"
                                ),
                            )
                        )
                action_text = (
                    "Stop exercise immediately, switch to backup O2."
                    if sev == Severity.critical
                    else "Reduce activity, check O2 supply."
                )
                msg = f"{crew.name}: Possible hypoxia indication ({sev.value}). {action_text}"
                self._active_alerts[hipoksi_id] = Alert(
                    id=hipoksi_id,
                    crew_id=cid,
                    metric=Metric.spo2,
                    severity=sev,
                    message=msg,
                    rule_id="karar-olasi_hipoksi",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[hipoksi_id],
                    last_seen=frame.ts,
                    why=why,
                )
        else:
            self._consecutive_counts[hipoksi_id] = 0
            self._alert_first_seen.pop(hipoksi_id, None)
            self._active_alerts.pop(hipoksi_id, None)

        co2_id = f"{cid}:karar-olasi_co2_etkisi"
        co2_r = frame.cabin.pp_co2
        co2_val = co2_r.value
        co2_ok = co2_val is not None and co2_r.quality == Quality.ok.value
        if co2_ok:
            assert co2_val is not None
            co2_mmhg = co2_val * MMHG_PER_KPA if co2_r.unit.lower() == "kpa" else co2_val
        else:
            co2_mmhg = None

        rr_r = vitals.resp_rate
        rr_ok = (
            rr_r.value is not None and rr_r.quality == Quality.ok.value and rr_r.baseline_delta is not None
        )

        co2_cond = (
            co2_ok
            and co2_mmhg is not None
            and rr_ok
            and rr_r.baseline_delta is not None
            and co2_mmhg > self.ppco2_sari_esik
            and rr_r.baseline_delta >= self.rr_izle
        )

        if co2_cond:
            assert co2_mmhg is not None
            assert rr_r.baseline_delta is not None
            sev = Severity.critical if "cabin:ppco2-limit" in self._active_alerts else Severity.caution
            count = self._consecutive_counts.get(co2_id, 0) + 1
            self._consecutive_counts[co2_id] = count
            if count == 1:
                self._alert_first_seen[co2_id] = frame.ts
            if count >= self.consecutive_required:
                base_rr = baselines[Metric.resp_rate]
                why = [
                    Evidence(
                        metric=Metric.pp_co2,
                        value=round(co2_mmhg, 2),
                        threshold=self.ppco2_sari_esik,
                        comparator=Comparator.gt,
                        ts=co2_r.ts,
                        note=(
                            f"Cabin ppCO2 {co2_mmhg:.2f} mmHg (threshold {self.ppco2_sari_esik} mmHg), "
                            f"for {count} readings"
                        ),
                    ),
                    Evidence(
                        metric=Metric.resp_rate,
                        value=rr_r.value,
                        threshold=round(base_rr + self.rr_izle, 1),
                        comparator=Comparator.ge,
                        ts=rr_r.ts,
                        note=(
                            f"Respiration rate {rr_r.baseline_delta:+.1f} breaths/min (threshold "
                            f"+{self.rr_izle:.1f}), for {count} readings"
                        ),
                    ),
                ]
                action_text = (
                    "Stop exercise, increase CO2 scrubbing."
                    if sev == Severity.critical
                    else "Reduce effort, increase CO2 scrubbing."
                )
                msg = f"{crew.name}: Possible CO2 effect ({sev.value}). {action_text}"
                self._active_alerts[co2_id] = Alert(
                    id=co2_id,
                    crew_id=cid,
                    metric=Metric.pp_co2,
                    severity=sev,
                    message=msg,
                    rule_id="karar-olasi_co2_etkisi",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[co2_id],
                    last_seen=frame.ts,
                    why=why,
                )
        else:
            self._consecutive_counts[co2_id] = 0
            self._alert_first_seen.pop(co2_id, None)
            self._active_alerts.pop(co2_id, None)

        temp_id = f"{cid}:karar-isi_sapmasi"
        temp_r = vitals.core_temp
        temp_ok = (
            temp_r.value is not None
            and temp_r.quality == Quality.ok.value
            and temp_r.baseline_delta is not None
        )
        temp_cond = temp_ok and temp_r.baseline_delta is not None and temp_r.baseline_delta >= self.temp_izle

        if temp_cond:
            assert temp_r.baseline_delta is not None
            sev = Severity.critical if temp_r.baseline_delta >= self.temp_eylem else Severity.caution
            count = self._consecutive_counts.get(temp_id, 0) + 1
            self._consecutive_counts[temp_id] = count
            if count == 1:
                self._alert_first_seen[temp_id] = frame.ts
            if count >= self.consecutive_required:
                base_temp = baselines[Metric.core_temp]
                thr_temp = base_temp + (self.temp_eylem if sev == Severity.critical else self.temp_izle)
                why = [
                    Evidence(
                        metric=Metric.core_temp,
                        value=temp_r.value,
                        threshold=round(thr_temp, 2),
                        comparator=Comparator.ge,
                        ts=temp_r.ts,
                        note=(
                            f"Body temperature {temp_r.baseline_delta:+.2f} °C from baseline "
                            f"(threshold +{self.temp_izle:.1f} °C), for {count} readings"
                        ),
                    )
                ]
                msg = f"{crew.name}: Body temperature deviation ({sev.value}). Rest, report to Earth."
                self._active_alerts[temp_id] = Alert(
                    id=temp_id,
                    crew_id=cid,
                    metric=Metric.core_temp,
                    severity=sev,
                    message=msg,
                    rule_id="karar-isi_sapmasi",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[temp_id],
                    last_seen=frame.ts,
                    why=why,
                )
        else:
            self._consecutive_counts[temp_id] = 0
            self._alert_first_seen.pop(temp_id, None)
            self._active_alerts.pop(temp_id, None)

        hr_id = f"{cid}:karar-kalp_yuku"
        hr_r = vitals.heart_rate
        hr_ok = (
            hr_r.value is not None and hr_r.quality == Quality.ok.value and hr_r.baseline_delta is not None
        )
        hr_cond = (
            crew.activity == Activity.resting.value
            and hr_ok
            and hr_r.baseline_delta is not None
            and hr_r.baseline_delta >= self.hr_izle
        )

        if hr_cond:
            assert hr_r.baseline_delta is not None
            sev = Severity.critical if hr_r.baseline_delta >= self.hr_eylem else Severity.caution
            count = self._consecutive_counts.get(hr_id, 0) + 1
            self._consecutive_counts[hr_id] = count
            if count == 1:
                self._alert_first_seen[hr_id] = frame.ts
            if count >= self.consecutive_required:
                base_hr = baselines[Metric.heart_rate]
                thr_hr = base_hr + (self.hr_eylem if sev == Severity.critical else self.hr_izle)
                why = [
                    Evidence(
                        metric=Metric.heart_rate,
                        value=hr_r.value,
                        threshold=round(thr_hr, 1),
                        comparator=Comparator.ge,
                        ts=hr_r.ts,
                        note=(
                            f"Resting heart rate {hr_r.baseline_delta:+.1f} bpm from baseline "
                            f"(threshold +{self.hr_izle:.1f} bpm), for {count} readings"
                        ),
                    )
                ]
                msg = f"{crew.name}: Elevated resting cardiac load ({sev.value}). Reduce effort, monitor."
                self._active_alerts[hr_id] = Alert(
                    id=hr_id,
                    crew_id=cid,
                    metric=Metric.heart_rate,
                    severity=sev,
                    message=msg,
                    rule_id="karar-kalp_yuku",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[hr_id],
                    last_seen=frame.ts,
                    why=why,
                )
        else:
            self._consecutive_counts[hr_id] = 0
            self._alert_first_seen.pop(hr_id, None)
            self._active_alerts.pop(hr_id, None)

        rad_id = f"{cid}:karar-radyasyon_etkisi"
        career_sari = self.sari_oran * self.kariyer_limiti
        career_triggered = crew.cumulative_dose_msv is not None and crew.cumulative_dose_msv >= career_sari
        career_critical = (
            crew.cumulative_dose_msv is not None and crew.cumulative_dose_msv >= self.kariyer_limiti
        )

        sep_active = sep is not None and sep.sep_active
        if sep_active:
            assert sep is not None
            if (
                crew.event_dose_msv is None
                or sep.event_dose_rate_msv_per_h is None
                or sep.remaining_event_h is None
            ):
                sep_missing = True
                sep_triggered = False
                sep_critical = False
                tahmini_doz = None
            else:
                sep_missing = False
                tahmini_doz = crew.event_dose_msv + sep.event_dose_rate_msv_per_h * sep.remaining_event_h
                spe_sari = self.sari_oran * self.spe_tasarim_esigi
                sep_triggered = (tahmini_doz >= spe_sari) or (crew.event_dose_msv >= spe_sari)
                sep_critical = (tahmini_doz >= self.spe_tasarim_esigi) or (
                    crew.event_dose_msv >= self.spe_tasarim_esigi
                )
        else:
            sep_missing = False
            sep_triggered = False
            sep_critical = False
            tahmini_doz = None

        rad_cond = career_triggered or sep_triggered

        if rad_cond:
            sev = Severity.critical if (career_critical or sep_critical) else Severity.caution
            count = self._consecutive_counts.get(rad_id, 0) + 1
            self._consecutive_counts[rad_id] = count
            if count == 1:
                self._alert_first_seen[rad_id] = frame.ts
            if count >= self.consecutive_required:
                why_rad: list[Evidence] = []
                if sep_triggered:
                    assert tahmini_doz is not None
                    thr = (
                        self.spe_tasarim_esigi
                        if sev == Severity.critical
                        else self.sari_oran * self.spe_tasarim_esigi
                    )
                    why_rad.append(
                        Evidence(
                            metric=Metric.event_dose,
                            value=crew.event_dose_msv,
                            threshold=round(thr, 1),
                            comparator=Comparator.ge,
                            ts=frame.ts,
                            note=(
                                f"Estimated event dose {tahmini_doz:.1f} mSv (threshold "
                                f"{self.sari_oran * self.spe_tasarim_esigi:.1f} mSv), for {count} readings"
                            ),
                        )
                    )
                if career_triggered:
                    thr = self.kariyer_limiti if sev == Severity.critical else career_sari
                    why_rad.append(
                        Evidence(
                            metric=Metric.event_dose,
                            value=crew.cumulative_dose_msv,
                            threshold=round(thr, 1),
                            comparator=Comparator.ge,
                            ts=frame.ts,
                            note=(
                                f"Cumulative dose {crew.cumulative_dose_msv:.1f} mSv "
                                f"(threshold {career_sari:.1f} mSv), for {count} readings"
                            ),
                        )
                    )
                action_text = (
                    "Move to storm shelter!"
                    if sev == Severity.critical
                    else "Prepare to move to shelter, start dose tracking."
                )
                msg = f"{crew.name}: Radiation exposure ({sev.value}). {action_text}"
                self._active_alerts[rad_id] = Alert(
                    id=rad_id,
                    crew_id=cid,
                    metric=Metric.event_dose,
                    severity=sev,
                    message=msg,
                    rule_id="karar-radyasyon_etkisi",
                    consecutive_count=count,
                    first_seen=self._alert_first_seen[rad_id],
                    last_seen=frame.ts,
                    why=why_rad,
                )
        else:
            self._consecutive_counts[rad_id] = 0
            self._alert_first_seen.pop(rad_id, None)
            self._active_alerts.pop(rad_id, None)

        return sep_missing

    def _finalize_cabin(self, cabin: CabinAtmosphere, pio2_mmhg: float | None) -> CabinAtmosphere:
        co2_r = cabin.pp_co2
        if co2_r.value is None or co2_r.quality != Quality.ok.value:
            co2_status = Status.unknown
        else:
            co2_mmhg = co2_r.value * MMHG_PER_KPA if co2_r.unit.lower() == "kpa" else co2_r.value
            if co2_mmhg > self.ppco2_limit_1saat_ort:
                co2_status = Status.critical
            elif co2_mmhg > self.ppco2_sari_esik:
                co2_status = Status.caution
            else:
                co2_status = Status.nominal
        updated_co2 = co2_r.model_copy(update={"status": co2_status})

        o2_r = cabin.pp_o2
        if o2_r.value is None or o2_r.quality != Quality.ok.value or pio2_mmhg is None:
            o2_status = Status.unknown
        else:
            if pio2_mmhg < self.pio2_hafif_hipoksi_alt:
                o2_status = Status.critical
            elif pio2_mmhg < self.pio2_normoksi_min or pio2_mmhg > self.pio2_normoksi_max:
                o2_status = Status.caution
            else:
                o2_status = Status.nominal
        updated_o2 = o2_r.model_copy(update={"status": o2_status})

        p_r = cabin.cabin_pressure
        if p_r.value is None or p_r.quality != Quality.ok.value:
            p_status = Status.unknown
        else:
            p_mmhg = p_r.value * MMHG_PER_KPA if p_r.unit.lower() == "kpa" else p_r.value
            if not (self.pressure_min < p_mmhg <= self.pressure_max):
                p_status = Status.caution
            else:
                p_status = Status.nominal
        updated_p = p_r.model_copy(update={"status": p_status})

        def reading_status(r: Reading) -> Status:
            return Status.unknown if (r.value is None or r.quality != Quality.ok.value) else Status.nominal

        updated_temp = cabin.cabin_temp.model_copy(update={"status": reading_status(cabin.cabin_temp)})
        updated_humidity = cabin.rel_humidity.model_copy(
            update={"status": reading_status(cabin.rel_humidity)}
        )
        updated_rad = cabin.radiation_dose_rate.model_copy(
            update={"status": reading_status(cabin.radiation_dose_rate)}
        )

        all_active_cabin_alerts = [
            a
            for a in list(self._active_alerts.values()) + list(self._missing_alerts.values())
            if a.crew_id is None
        ]

        if all_active_cabin_alerts:
            severities = {a.severity for a in all_active_cabin_alerts}
            if Severity.critical.value in severities:
                cab_status = Status.critical
            elif Severity.warning.value in severities:
                cab_status = Status.warning
            else:
                cab_status = Status.caution
        else:
            has_missing = any(
                r.value is None or r.quality != Quality.ok.value
                for r in (
                    cabin.pp_co2,
                    cabin.pp_o2,
                    cabin.cabin_pressure,
                    cabin.cabin_temp,
                    cabin.rel_humidity,
                    cabin.radiation_dose_rate,
                )
            )
            cab_status = Status.unknown if (has_missing or pio2_mmhg is None) else Status.nominal

        return cabin.model_copy(
            update={
                "pp_co2": updated_co2,
                "pp_o2": updated_o2,
                "cabin_pressure": updated_p,
                "cabin_temp": updated_temp,
                "rel_humidity": updated_humidity,
                "radiation_dose_rate": updated_rad,
                "status": cab_status,
            }
        )
