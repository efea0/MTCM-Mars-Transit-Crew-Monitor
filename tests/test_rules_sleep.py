"""Short-sleep rule: caution/critical after consecutive short sleep periods (HYPOTHESIS thresholds)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from backend import rules as r
from backend.models import (
    Metric,
    Quality,
    Reading,
    Severity,
    SleepSummary,
    TelemetryFrame,
)
from tests.test_rules import BASE_TIME, make_frame

ALERT_ID = "crew-1:karar-uyku_eksikligi"


def with_sleep(
    tick: int, period: int | None, hours: float | None, quality: Quality = Quality.ok
) -> TelemetryFrame:
    ts = BASE_TIME + timedelta(minutes=5 * tick)
    frame = make_frame(tick=tick, elapsed_s=300.0 * tick, ts=ts)
    sleep = None
    if period is not None:
        reading = Reading(
            value=hours,
            unit="h",
            ts=ts,
            quality=quality if hours is not None else Quality.missing,
        )
        sleep = SleepSummary(period=period, hours=reading)
    crew = [frame.crew[0].model_copy(update={"sleep": sleep}), *frame.crew[1:]]
    return frame.model_copy(update={"crew": crew})


def run(engine: r.RulesEngine, *steps: tuple[int | None, float | None]) -> TelemetryFrame:
    out = None
    for tick, (period, hours) in enumerate(steps):
        out = engine.evaluate(with_sleep(tick, period, hours))
    assert out is not None
    return out


def sleep_alert(frame: TelemetryFrame):
    return next((a for a in frame.alerts if a.id == ALERT_ID), None)


@pytest.fixture
def engine() -> r.RulesEngine:
    return r.RulesEngine()


def test_one_short_period_is_not_enough(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.5))
    assert sleep_alert(out) is None
    assert out.crew[0].sleep is not None and out.crew[0].sleep.hours.status.value == "caution"


def test_two_short_periods_raise_a_caution(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.5), (2, 5.8))
    alert = sleep_alert(out)
    assert alert is not None
    assert alert.severity == Severity.caution
    assert alert.metric == Metric.sleep_hours
    assert alert.rule_id == "karar-uyku_eksikligi"
    assert alert.consecutive_count == 2
    assert alert.why[0].threshold == 6.0 and alert.why[0].value == 5.8
    assert out.crew[0].decision.value == "watch"


def test_two_very_short_periods_raise_a_critical_and_action(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 4.5), (2, 4.0))
    alert = sleep_alert(out)
    assert alert is not None and alert.severity == Severity.critical
    assert alert.why[0].threshold == 5.0
    assert out.crew[0].decision.value == "action"


def test_caution_escalates_to_critical_after_two_very_short_periods(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.5), (2, 4.5))
    assert sleep_alert(out).severity == Severity.caution
    out = run(engine, (3, 4.2))
    assert sleep_alert(out).severity == Severity.critical


def test_the_same_period_is_counted_once(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.0), (1, 5.0), (1, 5.0), (1, 5.0))
    assert sleep_alert(out) is None


def test_a_good_period_clears_the_alert_and_the_count(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.0), (2, 5.0))
    assert sleep_alert(out) is not None
    out = run(engine, (3, 7.5))
    assert sleep_alert(out) is None and out.crew[0].decision.value == "normal"
    out = run(engine, (4, 5.0))
    assert sleep_alert(out) is None, "the count restarted after the good period"


def test_missing_sleep_data_neither_clears_nor_raises(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.0), (2, 5.0), (3, None))
    assert sleep_alert(out) is not None, "a data gap must not clear an active alert"
    assert out.crew[0].sleep.hours.status.value == "unknown"
    fresh = r.RulesEngine()
    out = run(fresh, (1, None), (2, None), (3, None))
    assert sleep_alert(out) is None


def test_no_sleep_data_at_all_is_quiet_and_clears(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.0), (2, 5.0))
    assert sleep_alert(out) is not None
    out = engine.evaluate(with_sleep(9, None, None))
    assert sleep_alert(out) is None and out.crew[0].sleep is None


def test_reset_forgets_sleep_history(engine: r.RulesEngine) -> None:
    run(engine, (1, 5.0))
    engine.reset()
    out = run(engine, (1, 5.0))
    assert sleep_alert(out) is None, "after reset the first short period is only one period"


def test_rule_is_off_when_the_reference_has_no_sleep_section(tmp_path: Path) -> None:
    data = json.loads(r.DEFAULT_RANGES.read_text(encoding="utf-8"))
    data.pop("uyku_kurallari")
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    out = run(r.RulesEngine(path), (1, 3.0), (2, 3.0))
    assert sleep_alert(out) is None


def test_inconsistent_thresholds_are_rejected(tmp_path: Path) -> None:
    data = json.loads(r.DEFAULT_RANGES.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in data["uyku_kurallari"]["kurallar"]}
    rows["sleep-short-critical"]["threshold"] = 7
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="critical threshold"):
        r.RulesEngine(path)
