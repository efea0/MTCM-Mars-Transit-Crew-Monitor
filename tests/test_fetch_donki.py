"""data/donki/fetch_donki.py ve kayıtlı sep_event.json için internetsiz testler — sahibi: Barış (qa)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

DONKI_DIR = Path(__file__).resolve().parent.parent / "data" / "donki"
SAVED = DONKI_DIR / "sep_event.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_donki", DONKI_DIR / "fetch_donki.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_donki"] = module
    spec.loader.exec_module(module)
    return module


fd = _load_module()

# Gerçek DONKI cevabındaki alanlar: sepID, eventTime, instruments, linkedEvents, link
SAMPLE: list[dict[str, Any]] = [
    {"sepID": "B", "eventTime": "2024-05-20T10:00Z", "linkedEvents": None, "link": "https://example/B"},
    {"sepID": "C", "eventTime": "2024-05-11T05:00Z", "linkedEvents": [{"activityID": "FLR-1"}]},
    {"sepID": "A", "eventTime": "2024-05-03T01:00Z", "linkedEvents": [{"activityID": "CME-1"}]},
]


def test_default_is_earliest_linked_event() -> None:
    assert fd.pick_event(SAMPLE)["sepID"] == "A"


def test_selection_is_independent_of_input_order() -> None:
    assert fd.pick_event(list(reversed(SAMPLE)))["sepID"] == "A"


def test_falls_back_to_earliest_when_nothing_linked() -> None:
    unlinked = [dict(e, linkedEvents=None) for e in SAMPLE]
    assert fd.pick_event(unlinked)["sepID"] == "A"


def test_prefers_linked_even_if_not_earliest() -> None:
    events = [
        {"sepID": "X", "eventTime": "2024-05-01T00:00Z", "linkedEvents": None},
        {"sepID": "Y", "eventTime": "2024-05-05T00:00Z", "linkedEvents": [{"activityID": "FLR-9"}]},
    ]
    assert fd.pick_event(events)["sepID"] == "Y"


GOES = [{"displayName": "GOES-P: SEISS >10 MeV"}]
GOES_100 = [{"displayName": "GOES-P: SEISS >100 MeV"}]
SOHO = [{"displayName": "SOHO: COSTEP 15.8-39.8 MeV"}]
MODEL = [{"displayName": "MODEL: REleASE:ACE/EPAM 15.8-39.8 MeV"}]
LINK = [{"activityID": "FLR-1"}]


def _ev(sep_id: str, time: str, instruments: list[dict[str, str]]) -> dict[str, Any]:
    return {"sepID": sep_id, "eventTime": time, "instruments": instruments, "linkedEvents": LINK}


def test_model_prediction_is_not_picked_when_measurement_exists() -> None:
    events = [_ev("M", "2024-05-09T13:59Z", MODEL), _ev("S", "2024-05-10T12:59Z", SOHO)]
    assert fd.pick_event(events)["sepID"] == "S"


def test_goes_10mev_is_preferred_over_earlier_measurement() -> None:
    events = [
        _ev("M", "2024-05-09T13:59Z", MODEL),
        _ev("S", "2024-05-10T12:59Z", SOHO),
        _ev("G", "2024-05-10T13:35Z", GOES),
        _ev("H", "2024-05-11T02:10Z", GOES_100),
    ]
    assert fd.pick_event(events)["sepID"] == "G"


def test_goes_100mev_alone_is_not_the_10mev_channel() -> None:
    assert not fd.is_goes_10mev(_ev("H", "2024-05-11T02:10Z", GOES_100))


def test_only_models_falls_back_to_earliest() -> None:
    events = [_ev("M2", "2024-05-13T13:10Z", MODEL), _ev("M1", "2024-05-09T13:59Z", MODEL)]
    assert fd.pick_event(events)["sepID"] == "M1"


def test_event_without_instruments_is_not_called_model() -> None:
    assert not fd.is_model({"sepID": "X", "instruments": None})


def test_explicit_sep_id() -> None:
    assert fd.pick_event(SAMPLE, "B")["sepID"] == "B"


@pytest.mark.parametrize(("events", "sep_id"), [(SAMPLE, "yok"), ([], None)])
def test_invalid_selection_raises(events: list[dict[str, Any]], sep_id: str | None) -> None:
    with pytest.raises(ValueError):
        fd.pick_event(events, sep_id)


def test_main_writes_file_and_never_stores_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "GIZLI_ANAHTAR_123"
    out = tmp_path / "donki" / "sep_event.json"
    monkeypatch.setenv("NASA_API_KEY", secret)
    monkeypatch.setattr(fd, "fetch_events", lambda *_: SAMPLE)
    assert fd.main(["--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert secret not in text
    payload = json.loads(text)
    assert payload["selected"]["sepID"] == "A"
    assert len(payload["raw_response"]) == len(SAMPLE)


def test_main_returns_1_on_empty_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "sep_event.json"
    monkeypatch.setattr(fd, "fetch_events", lambda *_: [])
    assert fd.main(["--out", str(out)]) == 1
    assert not out.exists()


# ---------------------------------------------------------------- kayıtlı gerçek veri


@pytest.fixture(scope="module")
def saved() -> dict[str, Any]:
    return json.loads(SAVED.read_text(encoding="utf-8"))


def test_saved_file_has_raw_response_and_fetch_time(saved: dict[str, Any]) -> None:
    assert saved["fetched_at"]
    assert saved["source"] == fd.DONKI_URL
    assert saved["raw_response"], "ham cevap boş olmamalı (AGENTS.md §5)"
    assert saved["selected"] in saved["raw_response"]


def test_saved_selection_is_reproducible(saved: dict[str, Any]) -> None:
    assert fd.pick_event(saved["raw_response"]) == saved["selected"]
    assert saved["selected"]["linkedEvents"], "seçilen olay bir patlamaya/CME'ye bağlı olmalı"


def test_saved_selection_is_goes_measurement_not_model(saved: dict[str, Any]) -> None:
    assert fd.is_goes_10mev(saved["selected"]), "jüri için ölçülmüş GOES >10 MeV olayı seçilmeli"
    assert not fd.is_model(saved["selected"])


def test_saved_file_contains_no_api_key() -> None:
    assert "api_key" not in SAVED.read_text(encoding="utf-8")
