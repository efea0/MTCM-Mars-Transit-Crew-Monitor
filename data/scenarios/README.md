# Scenario files

All values are simulated. Thresholds are aligned with `data/reference/ranges.json`, and the cabin balance with `backend/atmosphere.py` (#14).

| Scenario | Status | Duration (mission min) |
|---|---|---|
| `nominal` | Ready | 180 |
| `hypoxia` | Ready | 1800 |
| `hypercapnia` | Ready | 670 |
| `solar_storm` | Ready | 780 |
| `sensor_dropout` | Ready | 120 |
| `sleep_deprivation` | **Missing**, out of scope | - |

Common rules: every scenario fits in at most 1800 mission minutes (checked by a test), volume 75 m³,
measurement interval 5 mission minutes, the engine's limit-exceeded level is `critical`.

## Scenario that is not written

`models.Scenario` defines the name `sleep_deprivation`; its file does not exist yet. We state the gap here openly instead of hiding it.

- **`sleep_deprivation`:** `ranges.json` has no sleep-specific threshold (only the CO₂ effect note mentions "reduced sleep").
  The expected decision table cannot be written until thresholds are decided. The contract has `Activity.sleep` (#26).

## sensor_dropout

Three sensor states (`missing`, `stale`, `sensor_fault`) and the return of data. The expected behavior is in the
`expected_data_status` section: a reading whose quality is not `ok` becomes `unknown` from the first measurement and is never
counted as `nominal` (the `Reading` validator); after 3 consecutive invalid readings a "no data" alert (`missing-<metric>`) is
raised at `caution` level, and the `sensor_fault` value does not enter the rules engine.
The behavior is consistent with the rules engine (#43) and the contract (`Comparator.missing`); the rules are in the `data_quality_rules` section.

When a scenario is added, it must also be added to the `REQUIRED_SCENARIOS` set in `tests/test_scenarios.py`.
