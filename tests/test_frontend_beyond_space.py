"""Beyond space diyaloğu (frontend): profil tablosundaki sayılar data/reference/ranges.json ile aynı olmalı.

Diyalog yalnızca bilgidir; sayıları elle kopyalandığı için bu test ranges.json değişince kırılarak
ikisini senkron tutar. Ayrıca dürüstlük notlarının ve "planned" ifadesinin kalktığı yerde test kırılır.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RANGES = json.loads((ROOT / "data" / "reference" / "ranges.json").read_text(encoding="utf-8"))
SOURCE = (ROOT / "frontend" / "beyond-space.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
PROFILES = RANGES["profiller"]


def block(profile_id: str) -> str:
    match = re.search(rf'id: "{profile_id}".*?\n  \}},', SOURCE, re.S)
    assert match, f"beyond-space.js içinde {profile_id} profili yok"
    return match.group(0)


def test_every_prepared_profile_is_listed() -> None:
    for profile_id in PROFILES:
        assert f'id: "{profile_id}"' in SOURCE, profile_id


def test_volume_and_crew_match_ranges() -> None:
    for profile_id, profile in PROFILES.items():
        text = block(profile_id)
        assert f'volume: "{int(profile["hacim_m3"])} m³"' in text, profile_id
        assert f"crew: {int(profile['kisi'])}," in text, profile_id


def test_limits_match_ranges() -> None:
    mars = block("mars_transit")
    assert f'co2: "{PROFILES["mars_transit"]["co2_limit"]["deger"]:.1f} mmHg"' in mars
    assert f"PIO₂ ≥ {PROFILES['mars_transit']['o2_limit']['pio2_min']} mmHg" in mars
    sub = PROFILES["denizalti"]["co2_limit"]
    sub_text = block("denizalti")
    assert f"{sub['deger_yuzde']} %" in sub_text and f"{sub['deger_mmHg']} mmHg" in sub_text
    refuge = PROFILES["yeralti_siginagi"]
    refuge_text = block("yeralti_siginagi")
    co2 = refuge["co2_limit"]
    assert f"{co2['ortalama_yuzde']:.1f} % average" in refuge_text
    assert f"{co2['sapma_max_yuzde']} %" in refuge_text and f"{co2['ortalama_mmHg']} mmHg" in refuge_text
    o2 = refuge["o2_limit"]
    assert f"{o2['min_yuzde']} – {o2['max_yuzde']:.1f} %" in refuge_text


def test_only_mars_is_live_and_others_are_planned() -> None:
    """Motor yalnızca mars_transit'i çalıştırır; diğer profiller 'Planned' kalmalı."""
    assert 'engine: "Live"' in block("mars_transit")
    for profile_id in ("denizalti", "yeralti_siginagi"):
        assert 'engine: "Planned"' in block(profile_id)
    assert "planned, not built" in SOURCE


def test_honesty_notes_stay_visible() -> None:
    assert "not an official Navy limit" in block("denizalti")
    assert "CO and methane are not modeled" in block("yeralti_siginagi")


def test_dialog_is_information_only() -> None:
    """Diyalog telemetri okumaz ve API çağırmaz."""
    for forbidden in ("fetch(", "EventSource", "/api/"):
        assert forbidden not in SOURCE, forbidden


def test_header_trigger_is_wired() -> None:
    assert 'id="beyond-trigger"' in INDEX_HTML
    assert "beyond-space.css" in INDEX_HTML
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "createBeyondSpace" in app and 'getElementById("beyond-trigger")' in app
