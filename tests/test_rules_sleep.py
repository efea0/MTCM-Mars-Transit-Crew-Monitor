"""Short-sleep rules: caution and critical alerts after consecutive short sleep periods (HYPOTHESIS)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from backend import rules as r
from backend.models import Metric, Quality, Reading, Severity, SleepSummary, TelemetryFrame
from tests.test_rules import BASE_TIME, make_frame

CAUTION_ID = "crew-1:sleep-short-caution"
CRITICAL_ID = "crew-1:sleep-short-critical"


def with_sleep(
    tick: int, period: int | None, hours: float | None, quality: Quality = Quality.ok
) -> TelemetryFrame:
    ts = BASE_TIME + timedelta(minutes=5 * tick)
    frame = make_frame(tick=tick, elapsed_s=300.0 * tick, ts=ts)
    sleep = None
    if period is not None:
        reading = Reading(
            value=hours, unit="h", ts=ts, quality=quality if hours is not None else Quality.missing
        )
        sleep = SleepSummary(period=period, hours=reading)
    crew = [frame.crew[0].model_copy(update={"sleep": sleep}), *frame.crew[1:]]
    return frame.model_copy(update={"crew": crew})


def run(engine: r.RulesEngine, *steps: tuple[int | None, float | None], start: int = 0) -> TelemetryFrame:
    out = None
    for tick, (period, hours) in enumerate(steps, start=start):
        out = engine.evaluate(with_sleep(tick, period, hours))
    assert out is not None
    return out


def find(frame: TelemetryFrame, alert_id: str):
    return next((a for a in frame.alerts if a.id == alert_id), None)


@pytest.fixture
def engine() -> r.RulesEngine:
    return r.RulesEngine()


def test_one_short_period_is_not_enough(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.5))
    assert find(out, CAUTION_ID) is None and find(out, CRITICAL_ID) is None
    assert out.crew[0].sleep is not None and out.crew[0].sleep.hours.status == "caution"


def test_two_short_periods_raise_a_caution_and_watch(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.5), (2, 5.8))
    alert = find(out, CAUTION_ID)
    assert alert is not None and find(out, CRITICAL_ID) is None
    assert alert.severity == Severity.caution
    assert alert.metric == Metric.sleep_hours
    assert alert.rule_id == "sleep-short-caution"
    assert alert.consecutive_count == 2
    assert alert.why[0].threshold == 6.0 and alert.why[0].value == 5.8
    assert out.crew[0].decision == "watch"


def test_two_very_short_periods_raise_both_alerts_and_action(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 4.5), (2, 4.0))
    assert find(out, CAUTION_ID) is not None, "below 5 h is also below 6 h"
    critical = find(out, CRITICAL_ID)
    assert critical is not None and critical.severity == Severity.critical
    assert critical.why[0].threshold == 5.0
    assert out.crew[0].decision == "action"


def test_critical_needs_two_periods_below_five_hours(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.5), (2, 4.5))
    assert find(out, CAUTION_ID) is not None and find(out, CRITICAL_ID) is None
    out = run(engine, (3, 4.2), start=2)
    assert find(out, CRITICAL_ID) is not None


def test_the_same_period_is_counted_once(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.0), (1, 5.0), (1, 5.0), (1, 5.0))
    assert find(out, CAUTION_ID) is None


def test_a_good_period_clears_the_alerts_and_the_count(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.0), (2, 5.0))
    assert find(out, CAUTION_ID) is not None
    out = run(engine, (3, 7.5), start=2)
    assert find(out, CAUTION_ID) is None and out.crew[0].decision == "normal"
    out = run(engine, (4, 5.0), start=3)
    assert find(out, CAUTION_ID) is None, "the count restarted after the good period"


def test_missing_sleep_data_neither_clears_nor_raises(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.0), (2, 5.0), (3, None))
    assert find(out, CAUTION_ID) is not None, "a data gap must not clear an active alert"
    assert out.crew[0].sleep.hours.status == "unknown"
    out = run(r.RulesEngine(), (1, None), (2, None), (3, None))
    assert find(out, CAUTION_ID) is None


def test_no_sleep_data_at_all_is_quiet_and_clears(engine: r.RulesEngine) -> None:
    out = run(engine, (1, 5.0), (2, 5.0))
    assert find(out, CAUTION_ID) is not None
    out = engine.evaluate(with_sleep(9, None, None))
    assert find(out, CAUTION_ID) is None and out.crew[0].sleep is None


def test_reset_forgets_sleep_history(engine: r.RulesEngine) -> None:
    run(engine, (1, 5.0))
    engine.reset()
    out = run(engine, (1, 5.0))
    assert find(out, CAUTION_ID) is None, "after reset the first short period is only one period"


def test_rule_is_off_when_the_reference_has_no_sleep_section(tmp_path: Path) -> None:
    data = json.loads(r.DEFAULT_RANGES.read_text(encoding="utf-8"))
    data.pop("uyku_kurallari")
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    out = run(r.RulesEngine(path), (1, 3.0), (2, 3.0))
    assert find(out, CAUTION_ID) is None


def test_inconsistent_thresholds_are_rejected(tmp_path: Path) -> None:
    data = json.loads(r.DEFAULT_RANGES.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in data["uyku_kurallari"]["kurallar"]}
    rows["sleep-short-critical"]["threshold"] = 7
    path = tmp_path / "ranges.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="critical threshold"):
        r.RulesEngine(path)
