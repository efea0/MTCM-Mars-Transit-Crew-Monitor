"""Beyond space diyaloğu (frontend): profil tablosundaki sayılar data/reference/ranges.json ile aynı olmalı.

Diyalog yalnızca bilgidir; sayıları elle kopyalandığı için bu test ranges.json değişince kırılarak
ikisini senkron tutar. Ayrıca dürüstlük notlarının ve "planned" ifadesinin kalktığı yerde test kırılır.
"""

import json
import re
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------- worked example (refuge chamber failure)

GAS_CONSTANT = 8.314
PA_PER_MMHG = 133.322
TEMPERATURE_K = 295.0  # ranges.json sığınak açıklamasında yazılı başlangıç koşulu
START_O2_FRACTION = 0.21
START_CO2_FRACTION = 0.0004
HAS_EXAMPLE = "export const EXAMPLE" in SOURCE


def refuge_example() -> dict[str, float]:
    """Kütle dengesi aritmetiğini ranges.json değerlerinden yeniden hesaplar."""
    profile = PROFILES["yeralti_siginagi"]
    metabolic = RANGES["metabolik"]
    pressure = profile["toplam_basinc_mmHg"]
    volume, people = profile["hacim_m3"], profile["kisi"]
    per_kg = {
        gas: 1000 / molar * GAS_CONSTANT * TEMPERATURE_K / volume / PA_PER_MMHG
        for gas, molar in (("co2", 44), ("o2", 32))
    }
    co2_per_hour = people * metabolic["co2_uretimi"]["ortalama"] * per_kg["co2"] / 24
    o2_per_hour = people * metabolic["o2_tuketimi"]["ortalama"] * per_kg["o2"] / 24
    co2_limit = profile["co2_limit"]["ortalama_mmHg"]
    o2_low = profile["o2_limit"]["min_yuzde"] / 100 * pressure
    return {
        "co2_per_kg": per_kg["co2"],
        "o2_per_kg": per_kg["o2"],
        "co2_per_hour": co2_per_hour,
        "o2_per_hour": o2_per_hour,
        "co2_hours": (co2_limit - START_CO2_FRACTION * pressure) / co2_per_hour,
        "o2_hours": (START_O2_FRACTION * pressure - o2_low) / o2_per_hour,
    }


def hours_text(hours: float) -> str:
    whole = int(hours)
    return f"{whole} h {round((hours - whole) * 60)} min"


@pytest.mark.skipif(not HAS_EXAMPLE, reason="worked example is not in beyond-space.js yet")
def test_worked_example_numbers_match_a_recomputation_from_ranges() -> None:
    calc = refuge_example()
    assert (
        f"about {calc['co2_per_kg']:.1f} mmHg" in SOURCE and f"about {calc['o2_per_kg']:.1f} mmHg" in SOURCE
    )
    assert f"about {calc['co2_per_hour']:.1f} mmHg per hour" in SOURCE
    assert f"about {calc['o2_per_hour']:.1f} mmHg per hour" in SOURCE
    assert f"{calc['co2_hours']:.2f} h ({hours_text(calc['co2_hours'])})" in SOURCE
    assert f"{calc['o2_hours']:.2f} h ({hours_text(calc['o2_hours'])})" in SOURCE
    assert f'value: "about {calc["co2_hours"]:.1f} h"' in SOURCE
    assert f'value: "about {calc["o2_hours"]:.1f} h"' in SOURCE


@pytest.mark.skipif(not HAS_EXAMPLE, reason="worked example is not in beyond-space.js yet")
def test_worked_example_assumptions_match_ranges_and_say_it_is_not_the_engine() -> None:
    profile = PROFILES["yeralti_siginagi"]
    metabolic = RANGES["metabolik"]
    assert (
        f"{profile['kisi']} people in {int(profile['hacim_m3'])} m³ at {profile['toplam_basinc_mmHg']} mmHg"
        in SOURCE
    )
    assert (
        f"{metabolic['o2_tuketimi']['ortalama']} kg O₂ and {metabolic['co2_uretimi']['ortalama']} kg CO₂"
        in SOURCE
    )
    assert f"{profile['co2_limit']['ortalama_mmHg']} mmHg" in SOURCE
    assert f"not below {profile['o2_limit']['min_yuzde']} %" in SOURCE
    assert "not the live engine" in SOURCE
    assert "CO and methane are not modeled" in SOURCE
    description = profile["ariza_senaryosu"]["aciklama"]
    assert "about 1.3 h" in description and "about 2.8 h" in description
