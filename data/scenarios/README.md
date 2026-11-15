# Scenario files

All values are simulated. Thresholds are aligned with `data/reference/ranges.json`, and the cabin balance with `backend/atmosphere.py` (#14).

| Scenario | Status | Duration (mission min) |
|---|---|---|
| `nominal` | Ready | 180 |
| `hypoxia` | Ready | 1800 |
| `hypercapnia` | Ready | 670 |
| `solar_storm` | Ready | 780 |
| `sensor_dropout` | Ready | 120 |
| `sleep_deprivation` | Ready (thresholds are a hypothesis) | 1800 |

Common rules: every scenario fits in at most 1800 mission minutes (checked by a test), volume 75 m³,
measurement interval 5 mission minutes, the engine's limit-exceeded level is `critical`.

## sleep_deprivation

Sleep length per person: it is measured at the end of each sleep period (the sum of `activity = sleep` durations), and the
rule needs **2 periods in a row**: below 6 hours is Watch (`caution`), below 5 hours is Action (`critical`). The thresholds are
a **hypothesis** (team decision, unverified) and live in `ranges.json → uyku_kurallari.kurallar` (`sleep-short-caution`,
`sleep-short-critical`); `ranges.json` calls the metric `uyku_suresi`, the scenario uses the contract-style name `sleep_hours`.
Four people, four outcomes: Sally reaches Action, Katherine Watch, Neil is healthy, and John's single short sleep raises no
alert (the "in a row" condition).

What is and is not covered:
- The scenario file loads and runs in the real simulator (`activity` events carry empty `targets` and `ramp_min: 0`).
- The expected alerts are checked against an independent reference rule; the thresholds are compared with `ranges.json`.
- The engine does not produce sleep alerts yet and `sleep_hours` is not a contract `Metric` (known gap, xfail test, Aydın).

## sensor_dropout

Three sensor states (`missing`, `stale`, `sensor_fault`) and the return of data. The expected behavior is in the
`expected_data_status` section: a reading whose quality is not `ok` becomes `unknown` from the first measurement and is never
counted as `nominal` (the `Reading` validator); after 3 consecutive invalid readings a "no data" alert (`missing-<metric>`) is
raised at `caution` level, and the `sensor_fault` value does not enter the rules engine.
The behavior is consistent with the rules engine (#43) and the contract (`Comparator.missing`); the rules are in the `data_quality_rules` section.

When a scenario is added, it must also be added to the `REQUIRED_SCENARIOS` set in `tests/test_scenarios.py`.
