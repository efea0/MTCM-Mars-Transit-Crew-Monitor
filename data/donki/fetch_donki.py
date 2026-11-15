"""Fetch one solar energetic particle (SEP) event from DONKI and save it to data/donki/sep_event.json.
Owner: Baris (qa).

The event is stored in advance so the demo does not depend on the internet. DONKI provides the event
time and linked events, NOT THE DOSE; dose is modelled in the simulation.

Usage (from the repo root):
    python data/donki/fetch_donki.py
    python data/donki/fetch_donki.py --start 2024-05-01 --end 2024-05-31
    python data/donki/fetch_donki.py --sep-id 2024-05-10T13:35:00-SEP-001

API key: NASA_API_KEY environment variable; DEMO_KEY if unset. The key is never written to the file
or the repo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DONKI_URL = "https://api.nasa.gov/DONKI/SEP"
DEFAULT_OUT = Path(__file__).resolve().parent / "sep_event.json"
DEFAULT_START = "2024-05-01"
DEFAULT_END = "2024-05-31"
TIMEOUT_S = 30
HTTP_TOO_MANY_REQUESTS = 429
MODEL_PREFIX = "MODEL:"
GOES_10MEV = ">10 MeV"

Event = dict[str, Any]


def fetch_events(start: str, end: str, api_key: str) -> list[Event]:
    """Return DONKI SEP events as a list."""
    query = urllib.parse.urlencode({"startDate": start, "endDate": end, "api_key": api_key})
    with urllib.request.urlopen(f"{DONKI_URL}?{query}", timeout=TIMEOUT_S) as resp:
        return json.load(resp)


def _instrument_names(event: Event) -> list[str]:
    return [i.get("displayName", "") for i in event.get("instruments") or []]


def is_model(event: Event) -> bool:
    """Was the event reported only by model prediction (e.g. "MODEL: REleASE:...")?"""
    names = _instrument_names(event)
    return bool(names) and all(n.startswith(MODEL_PREFIX) for n in names)


def is_goes_10mev(event: Event) -> bool:
    """Was the event measured in the GOES >10 MeV channel? The NOAA S-scale is based on this channel."""
    return any(n.startswith("GOES") and GOES_10MEV in n for n in _instrument_names(event))


def pick_event(events: list[Event], sep_id: str | None = None) -> Event:
    """Pick a single event for the demo.

    If sep_id is given, that event. Otherwise, among events with linked events (flare/CME), in order:
    1. earliest event measured in the GOES >10 MeV channel,
    2. earliest non-model-predicted (measured) event,
    3. earliest event.
    If no event has links, the same order is applied to all events.
    The order is stable: the same data always yields the same event on every run.
    """
    if not events:
        raise ValueError("No events found in this date range.")
    if sep_id:
        for event in events:
            if event.get("sepID") == sep_id:
                return event
        raise ValueError(f"sepID not found: {sep_id}")
    ordered = sorted(events, key=lambda e: e.get("eventTime", ""))
    pool = [e for e in ordered if e.get("linkedEvents")] or ordered
    goes = [e for e in pool if is_goes_10mev(e)]
    measured = [e for e in pool if not is_model(e)]
    return (goes or measured or pool)[0]


def build_payload(events: list[Event], selected: Event, start: str, end: str) -> dict[str, Any]:
    """Payload written to the file. The API key is deliberately excluded."""
    return {
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": DONKI_URL,
        "query": {"startDate": start, "endDate": end},
        "note": "DONKI provides event time and type, not dose. Dose is simulated.",
        "selected": selected,
        "raw_response": events,
    }


def save(payload: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    out_path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--sep-id", default=None, help="Pick a specific event")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    try:
        events = fetch_events(args.start, args.end, api_key)
        selected = pick_event(events, args.sep_id)
    except urllib.error.HTTPError as err:
        hint = ""
        if err.code == HTTP_TOO_MANY_REQUESTS:
            hint = " (DEMO_KEY rate limit possible; wait or set NASA_API_KEY)"
        print(f"DONKI error: HTTP {err.code}{hint}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    save(build_payload(events, selected, args.start, args.end), args.out)
    print(f"{len(events)} events fetched. Selected: {selected.get('sepID')} ({selected.get('eventTime')})")
    print(f"Saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
