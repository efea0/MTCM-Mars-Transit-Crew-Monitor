"""DONKI'den bir güneş parçacık olayı (SEP) çekip data/donki/sep_event.json'a kaydeder — sahibi: Barış (qa).

Demo internete bağlı kalmasın diye olay önceden dosyaya alınır. DONKI olayın zamanını ve bağlantılı
olayları verir, DOZ VERMEZ; doz simülasyonda modellenir.

Kullanım (repo kökünden):
    python data/donki/fetch_donki.py
    python data/donki/fetch_donki.py --start 2024-05-01 --end 2024-05-31
    python data/donki/fetch_donki.py --sep-id 2024-05-09T13:59:00-SEP-001

API anahtarı: NASA_API_KEY ortam değişkeni; yoksa DEMO_KEY. Anahtar dosyaya yazılmaz, repoya konmaz.
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

Event = dict[str, Any]


def fetch_events(start: str, end: str, api_key: str) -> list[Event]:
    """DONKI SEP olaylarını liste olarak döndürür."""
    query = urllib.parse.urlencode({"startDate": start, "endDate": end, "api_key": api_key})
    with urllib.request.urlopen(f"{DONKI_URL}?{query}", timeout=TIMEOUT_S) as resp:
        return json.load(resp)


def pick_event(events: list[Event], sep_id: str | None = None) -> Event:
    """Demo için tek olay seçer.

    sep_id verilirse o olay; verilmezse bağlantılı olayı (flare/CME) olan en erken olay.
    Sıralama sabittir: aynı veride her çalıştırmada aynı olay çıkar.
    """
    if not events:
        raise ValueError("Bu tarih aralığında olay bulunamadı.")
    if sep_id:
        for event in events:
            if event.get("sepID") == sep_id:
                return event
        raise ValueError(f"sepID bulunamadı: {sep_id}")
    ordered = sorted(events, key=lambda e: e.get("eventTime", ""))
    linked = [e for e in ordered if e.get("linkedEvents")]
    return (linked or ordered)[0]


def build_payload(events: list[Event], selected: Event, start: str, end: str) -> dict[str, Any]:
    """Dosyaya yazılacak içerik. API anahtarı bilerek dahil edilmez."""
    return {
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": DONKI_URL,
        "query": {"startDate": start, "endDate": end},
        "note": "DONKI olay zamanı ve türü verir, doz vermez. Doz simüle edilir.",
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
    parser.add_argument("--sep-id", default=None, help="Belirli bir olayı seç")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    try:
        events = fetch_events(args.start, args.end, api_key)
        selected = pick_event(events, args.sep_id)
    except urllib.error.HTTPError as err:
        hint = ""
        if err.code == HTTP_TOO_MANY_REQUESTS:
            hint = " (DEMO_KEY istek sınırı olabilir; bekleyin ya da NASA_API_KEY tanımlayın)"
        print(f"DONKI hata verdi: HTTP {err.code}{hint}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, ValueError) as err:
        print(f"Hata: {err}", file=sys.stderr)
        return 1

    save(build_payload(events, selected, args.start, args.end), args.out)
    print(f"{len(events)} olay geldi. Seçilen: {selected.get('sepID')} ({selected.get('eventTime')})")
    print(f"Kaydedildi: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
