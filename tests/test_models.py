from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend import models as m

T0 = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def reading(value=1.0, **kw):
    return m.Reading(value=value, unit="x", ts=T0, **kw)


def crew_member(i):
    v = {f: reading() for f in m.CrewVitals.model_fields}
    return m.CrewMember(id=f"crew-{i}", name=f"A{i}", role="Pilot", vitals=m.CrewVitals(**v))


def cabin():
    return m.CabinAtmosphere(**{f: reading() for f in m.CabinAtmosphere.model_fields if f != "status"})


def evidence():
    return m.Evidence(metric="spo2", value=88.0, threshold=92.0, comparator="<", ts=T0, note="3 ölçüm")


def alert(**kw):
    base = dict(
        id="a1", crew_id="crew-1", metric="spo2", severity="warning", message="SpO2 düşük",
        rule_id="spo2-low", consecutive_count=3, first_seen=T0, last_seen=T0, why=[evidence()],
    )
    base.update(kw)
    return m.Alert(**base)


def test_contract_json_is_up_to_date():
    on_disk = m.CONTRACT_PATH.read_text(encoding="utf-8")
    assert on_disk == m.render_contract(), "python -m backend.models ile yeniden üret"


def test_missing_value_is_never_normal():
    with pytest.raises(ValidationError):
        reading(None)  # quality varsayılanı ok
    with pytest.raises(ValidationError):
        reading(None, quality="missing", status="nominal")
    with pytest.raises(ValidationError):
        reading(97.0, quality="stale", status="nominal")
    assert reading(None, quality="missing").status == "unknown"


def test_alert_requires_evidence_and_time_order():
    assert alert().why[0].metric == "spo2"
    with pytest.raises(ValidationError):
        alert(why=[])
    with pytest.raises(ValidationError):
        alert(last_seen=T0 - timedelta(seconds=1))


def test_frame_needs_exactly_four_crew():
    kw = dict(tick=0, ts=T0, mission_elapsed_s=0, scenario="nominal", cabin=cabin(), comms_one_way_delay_s=600)
    assert len(m.TelemetryFrame(crew=[crew_member(i) for i in range(1, 5)], **kw).crew) == 4
    with pytest.raises(ValidationError):
        m.TelemetryFrame(crew=[crew_member(i) for i in range(1, 4)], **kw)


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        m.Reading(value=1.0, unit="x", ts=T0, extra_field=1)
