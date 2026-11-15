"""Tests for the read-only /api/limits and /api/sep-event endpoints."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import RANGES_PATH, SEP_EVENT_PATH, app
from backend.reference import map_status, normalize_source

SEP_ID = "2024-05-10T13:35:00-SEP-001"


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def ranges() -> dict[str, Any]:
    return json.loads(RANGES_PATH.read_text(encoding="utf-8"))


def _by_group(items: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    return [item for item in items if item["group"] == group]


def test_limits_shape_and_counts(client: TestClient, ranges: dict[str, Any]) -> None:
    res = client.get("/api/limits")
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == ranges["meta"]["surum"]
    items = body["items"]
    assert len(_by_group(items, "cabin")) == 5
    assert len(_by_group(items, "vitals")) == 8
    assert len(_by_group(items, "radiation")) == 4
    assert len({item["id"] for item in items}) == len(items)
    keys = {
        "id",
        "group",
        "label",
        "metric",
        "severity",
        "comparator",
        "threshold",
        "unit",
        "consecutive_required",
        "source",
        "status",
    }
    assert all(set(item) == keys for item in items)


def test_limits_cabin_matches_ranges(client: TestClient, ranges: dict[str, Any]) -> None:
    cabin = _by_group(client.get("/api/limits").json()["items"], "cabin")
    assert [item["id"] for item in cabin] == [rule["id"] for rule in ranges["kurallar"]]
    for item, rule in zip(cabin, ranges["kurallar"], strict=True):
        assert item["metric"] == rule["metric"]
        assert item["severity"] == rule["severity"]
        assert item["comparator"] == rule["comparator"]
        assert item["threshold"] == rule["threshold"]
        assert item["consecutive_required"] == rule["consecutive_required"]
        assert item["status"] == map_status(rule["status"])
        assert item["source"] == normalize_source(rule["source"])
    assert cabin[0]["label"] == "Inspired O2 pressure below normoxia range"
    assert "Tablo" not in cabin[0]["source"]
    assert "Table 6.2-1" in cabin[0]["source"]


def test_limits_status_mapping(client: TestClient) -> None:
    items = {item["id"]: item for item in client.get("/api/limits").json()["items"]}
    assert items["pio2-normoxia-low"]["status"] == "verified"
    assert items["ppco2-caution"]["status"] == "partial"
    assert items["spo2-drop-watch"]["status"] == "unverified"
    assert map_status("doğrulandı") == "verified"
    assert map_status("kısmen") == "partial"
    assert map_status("doğrulanmadı") == "unverified"
    assert map_status("something else") == "unverified"
    assert map_status(None) == "unverified"


def test_limits_source_normalization() -> None:
    assert normalize_source("NASA-STD-3001 Tablo 6.2-1") == "NASA-STD-3001 Table 6.2-1"
    assert normalize_source("Bölüm 11.3") == "Section 11.3"
    assert normalize_source("varsayım (Ekip kararı)") == "assumption (team decision)"
    assert normalize_source(None) == "assumption (team decision)"


def test_limits_vitals_match_ranges(client: TestClient, ranges: dict[str, Any]) -> None:
    vitals = {item["id"]: item for item in _by_group(client.get("/api/limits").json()["items"], "vitals")}
    params = ranges["vital_karar_parametreleri"]
    expected = {
        "spo2-drop": ("spo2_dusus", -1, "<="),
        "hr-rise": ("nabiz_artis_dinlenme", 1, ">="),
        "rr-rise": ("solunum_artis", 1, ">="),
        "temp-rise": ("isi_artis", 1, ">="),
    }
    for prefix, (key, sign, comparator) in expected.items():
        for suffix, level, severity in (("watch", "izle", "caution"), ("action", "eylem", "critical")):
            item = vitals[f"{prefix}-{suffix}"]
            assert item["threshold"] == sign * params[key][level]
            assert item["comparator"] == comparator
            assert item["severity"] == severity
            assert item["consecutive_required"] == params["art_arda_olcum"]["deger"]
            assert item["status"] == map_status(params[key]["status"])
    assert vitals["spo2-drop-watch"]["threshold"] < 0


def test_limits_radiation_thresholds_derived_from_ranges(client: TestClient, ranges: dict[str, Any]) -> None:
    items = {item["id"]: item for item in _by_group(client.get("/api/limits").json()["items"], "radiation")}
    career = ranges["radyasyon"]["kariyer_limiti"]["deger"]
    spe = ranges["radyasyon"]["spe_tasarim_esigi"]["deger"]
    ratio = ranges["vital_karar_parametreleri"]["sari_kademe_genel"]["oran"]
    assert items["radiation-career-limit"]["threshold"] == career
    assert items["radiation-spe-limit"]["threshold"] == spe
    assert items["radiation-career-watch"]["threshold"] == pytest.approx(ratio * career)
    assert items["radiation-spe-watch"]["threshold"] == pytest.approx(ratio * spe)
    assert items["radiation-spe-watch"]["threshold"] == 200
    assert items["radiation-career-watch"]["threshold"] == 480
    assert all(item["unit"] == "mSv" for item in items.values())
    assert items["radiation-spe-watch"]["severity"] == "caution"
    assert items["radiation-spe-limit"]["severity"] == "critical"


def test_limits_skips_missing_keys_without_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    partial = tmp_path / "ranges.json"
    partial.write_text(
        json.dumps({"kurallar": [{"id": "broken"}], "radyasyon": {"kariyer_limiti": {"deger": 600}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "RANGES_PATH", partial)
    res = client.get("/api/limits")
    assert res.status_code == 200
    body = res.json()
    assert body["version"] is None
    assert [item["id"] for item in body["items"]] == ["radiation-career-limit"]
    assert body["items"][0]["status"] == "unverified"


def test_limits_missing_file_is_not_a_server_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main, "RANGES_PATH", tmp_path / "missing.json")
    assert client.get("/api/limits").status_code == 404


def test_sep_event_matches_recorded_file(client: TestClient) -> None:
    record = json.loads(SEP_EVENT_PATH.read_text(encoding="utf-8"))
    selected = record["selected"]
    res = client.get("/api/sep-event")
    assert res.status_code == 200
    body = res.json()
    assert body["sep_id"] == selected["sepID"] == SEP_ID
    assert body["event_time"] == selected["eventTime"]
    assert body["instruments"] == [i["displayName"] for i in selected["instruments"]]
    assert body["link"] == selected["link"]
    assert body["source"] == record["source"]
    assert body["fetched_at"] == record["fetched_at"]
    assert body["note"].startswith("Event time and type come from a real NASA DONKI record")


def test_sep_event_linked_events(client: TestClient) -> None:
    record = json.loads(SEP_EVENT_PATH.read_text(encoding="utf-8"))
    ids = [e["activityID"] for e in record["selected"]["linkedEvents"]]
    linked = client.get("/api/sep-event").json()["linked_events"]
    assert len(linked) == len(ids)
    assert {event["activity_id"] for event in linked} == set(ids)
    times = [event["time"] for event in linked]
    assert times == sorted(times)
    assert all(time.endswith("Z") for time in times)
    first = linked[0]
    assert first == {
        "activity_id": "2024-05-08T04:20:00-FLR-001",
        "type": "FLR",
        "time": "2024-05-08T04:20:00Z",
    }
    assert {event["type"] for event in linked} == {"FLR", "CME"}
    assert [e for e in linked if e["type"] == "CME"][0]["time"] == "2024-05-08T05:36:00Z"


def test_sep_event_sorts_and_skips_malformed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "sep_event.json"
    path.write_text(
        json.dumps(
            {
                "source": "test",
                "fetched_at": "2026-01-01T00:00:00+00:00",
                "selected": {
                    "sepID": "x",
                    "eventTime": "2024-05-10T13:35Z",
                    "linkedEvents": [
                        {"activityID": "2024-05-09T01:00:00-CME-001"},
                        {"activityID": "not-an-id"},
                        {"activityID": "2024-05-08T01:00:00-FLR-002"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "SEP_EVENT_PATH", path)
    body = client.get("/api/sep-event").json()
    assert [e["type"] for e in body["linked_events"]] == ["FLR", "CME"]
    assert body["instruments"] == []


def test_sep_event_missing_file_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main, "SEP_EVENT_PATH", tmp_path / "missing.json")
    res = client.get("/api/sep-event")
    assert res.status_code == 404
    assert res.json() == {"detail": "SEP event record not found."}


def test_reference_endpoints_need_no_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_CONTROL_KEY", "secret")
    assert client.get("/api/limits").status_code == 200
    assert client.get("/api/sep-event").status_code == 200
