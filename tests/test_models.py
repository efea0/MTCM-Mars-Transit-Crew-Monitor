from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend import models as m

T0 = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def reading(value=1.0, **kw):
    return m.Reading(value=value, unit="x", ts=T0, **kw)


def crew_member(i):
    v = {f: reading() for f in m.CrewVitals.model_fields}
    return m.CrewMember(id=f"crew-{i}", name=f"A{i}", role="Pilot", vitals=m.CrewVitals(**v))


def cabin():
    return m.CabinAtmosphere(
        **{f: reading() for f, field in m.CabinAtmosphere.model_fields.items() if field.is_required()}
    )


def evidence():
    return m.Evidence(metric="spo2", value=88.0, threshold=92.0, comparator="<", ts=T0, note="3 ölçüm")


def alert(**kw):
    base = dict(
        id="a1",
        crew_id="crew-1",
        metric="spo2",
        severity="warning",
        message="SpO2 düşük",
        rule_id="spo2-low",
        consecutive_count=3,
        first_seen=T0,
        last_seen=T0,
        why=[evidence()],
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
    kw = dict(
        tick=0, ts=T0, mission_elapsed_s=0, scenario="nominal", cabin=cabin(), comms_one_way_delay_s=600
    )
    assert len(m.TelemetryFrame(crew=[crew_member(i) for i in range(1, 5)], **kw).crew) == 4
    with pytest.raises(ValidationError):
        m.TelemetryFrame(crew=[crew_member(i) for i in range(1, 4)], **kw)


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        m.Reading(value=1.0, unit="x", ts=T0, extra_field=1)


def test_new_crew_fields_have_safe_defaults():
    member = crew_member(1)
    assert member.activity == "unknown"
    assert member.cumulative_dose_msv is None
    assert member.event_dose_msv is None
    assert reading().baseline_delta is None
    assert "pio2" in m.Metric.__members__ and "event_dose" in m.Metric.__members__


def test_cabin_limit_hours_default_to_none() -> None:
    atmosphere = cabin()
    assert atmosphere.hours_until_o2_limit is None
    assert atmosphere.hours_until_co2_limit is None


@pytest.mark.parametrize("field", ["hours_until_o2_limit", "hours_until_co2_limit"])
@pytest.mark.parametrize("hours", [None, 0.0, 2.5])
def test_cabin_limit_hours_accept_null_and_nonnegative_values(field: str, hours: float | None) -> None:
    atmosphere = m.CabinAtmosphere.model_validate({**cabin().model_dump(), field: hours})
    assert getattr(atmosphere, field) == hours
    assert atmosphere.model_dump(mode="json")[field] == hours


@pytest.mark.parametrize("field", ["hours_until_o2_limit", "hours_until_co2_limit"])
def test_cabin_limit_hours_reject_negative_values(field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        m.CabinAtmosphere.model_validate({**cabin().model_dump(), field: -0.1})
    assert exc_info.value.errors()[0]["loc"] == (field,)
    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"


def test_crew_decision_defaults_to_unknown_with_missing_data() -> None:
    member = crew_member(1)
    assert member.decision == m.CrewDecision.unknown
    payload = member.model_dump(exclude={"decision"})
    payload["vitals"] = {
        field: reading(None, quality="missing").model_dump() for field in m.CrewVitals.model_fields
    }
    assert m.CrewMember.model_validate(payload).decision == m.CrewDecision.unknown
    assert member.model_dump(mode="json")["decision"] == "unknown"


def test_crew_decision_enum_values() -> None:
    assert [decision.value for decision in m.CrewDecision] == ["normal", "watch", "action", "unknown"]


@pytest.mark.parametrize("decision", ["normal", "watch", "action", "unknown", *m.CrewDecision])
def test_crew_decision_accepts_enum_values(decision: str) -> None:
    member = m.CrewMember.model_validate({**crew_member(1).model_dump(), "decision": decision})
    assert member.decision == decision
    assert member.model_dump(mode="json")["decision"] == decision


@pytest.mark.parametrize("decision", ["nominal", "invalid", None])
def test_crew_decision_rejects_invalid_values(decision: str | None) -> None:
    with pytest.raises(ValidationError):
        m.CrewMember.model_validate({**crew_member(1).model_dump(), "decision": decision})


def test_activity_and_doses_are_validated():
    v = {f: reading() for f in m.CrewVitals.model_fields}
    base = dict(id="crew-1", name="A", role="Pilot", vitals=m.CrewVitals(**v))
    assert m.CrewMember(**base, activity="exercise").activity == "exercise"
    with pytest.raises(ValidationError):
        m.CrewMember(**base, activity="running")
    with pytest.raises(ValidationError):
        m.CrewMember(**base, cumulative_dose_msv=-1.0)
    with pytest.raises(ValidationError):
        m.CrewMember(**base, event_dose_msv=-0.1)


def test_missing_value_has_no_baseline_delta():
    assert reading(15.0, baseline_delta=15.0).baseline_delta == 15.0
    with pytest.raises(ValidationError):
        reading(None, quality="missing", baseline_delta=1.0)


def rule(**kw):
    base = dict(
        id="hr-rise-caution",
        metric="heart_rate",
        severity="caution",
        comparator=">=",
        threshold=15.0,
        consecutive_required=3,
        source="HİPOTEZ",
    )
    base.update(kw)
    return m.ThresholdRule(**base)


def test_threshold_rule_new_fields_default_to_absolute_instant_rule():
    r = rule()
    assert r.average_window_s is None
    assert r.relative_to_baseline is False
    assert r.skip_when == []


def test_threshold_rule_personal_and_windowed_options():
    r = rule(relative_to_baseline=True, skip_when=["exercise", "sleep"])
    assert r.relative_to_baseline is True and r.skip_when == ["exercise", "sleep"]
    assert rule(metric="pp_co2", average_window_s=3600).average_window_s == 3600
    assert rule(metric="pio2", severity="critical", comparator="<", threshold=127.0).metric == "pio2"
    with pytest.raises(ValidationError):
        rule(average_window_s=0)
    with pytest.raises(ValidationError):
        rule(skip_when=["running"])


def test_every_metric_has_a_unit() -> None:
    assert set(m.METRIC_UNITS) == set(m.Metric)


@pytest.mark.parametrize("metric", ["pp_o2", "pp_co2", "cabin_pressure", "pio2"])
def test_pressure_metrics_are_mmhg(metric: str) -> None:
    # Birim sessizce kPa'ya dönerse eşikler (mmHg) 7,5 kat yanlış okunur.
    assert m.METRIC_UNITS[m.Metric(metric)] == "mmHg"
