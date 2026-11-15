"""frontend/css/tokens.css için WCAG 2.x AA kontrast denetimi ve token disiplini — sahibi: Barış (qa).

Kontrast göreli parlaklık formülüyle (WCAG 2.x, sRGB) bu dosyada hesaplanır; WebAIM aracıyla aynı formüldür.
Denetlenenler:
1. Açık tema iki yerde tanımlı (sistem tercihi ve elle seçim); ikisi birebir aynı olmalı.
2. Her temada metin, durum, vurgu ve grafik renk çiftleri AA eşiğini geçer.
3. style.css rastgele HEX/RGB değeri taşımaz; renkler tokenlardan gelir.
4. Font dosyaları ve lisans metni yerinde.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
TOKENS = (FRONTEND / "css" / "tokens.css").read_text(encoding="utf-8")
STYLE = (FRONTEND / "style.css").read_text(encoding="utf-8")

NORMAL_TEXT = 4.5
GRAPHIC = 3.0
STATUSES = ("ok", "caution", "warning", "critical", "unknown")


def block(source: str, opener: str) -> str:
    start = source.index(opener) + len(opener)
    depth, index = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        index += 1
    return source[start : index - 1]


def declarations(css: str) -> dict[str, str]:
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", css)
        if "{" not in value
    }


DARK = declarations(block(TOKENS, ":root {"))
LIGHT_MANUAL = declarations(block(TOKENS, ':root[data-theme="light"] {'))
LIGHT_SYSTEM = declarations(
    block(block(TOKENS, "@media (prefers-color-scheme: light) {"), ':root:not([data-theme="dark"]) {')
)
THEMES = {"dark": DARK, "light": {**DARK, **LIGHT_MANUAL}}


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def pairs(tokens: dict[str, str]) -> list[tuple[str, str, float]]:
    surfaces = ("bg", "surface", "surface-raised", "surface-hover")
    out = [(fg, bg, NORMAL_TEXT) for fg in ("text", "muted", "accent-ink") for bg in surfaces]
    out += [("on-accent", "accent", NORMAL_TEXT)]
    out += [("text", f"{status}-tint", NORMAL_TEXT) for status in STATUSES]
    for status in STATUSES:
        out += [(f"{status}-ink", bg, NORMAL_TEXT) for bg in (*surfaces[1:], f"{status}-tint")]
    out += [("on-status", status, NORMAL_TEXT) for status in ("caution", "warning")]
    critical_foreground = "on-critical" if "--on-critical" in tokens else "on-status"
    out += [(critical_foreground, "critical", NORMAL_TEXT)]
    out += [("focus", bg, GRAPHIC) for bg in ("bg", "surface")]
    out += [("chart-baseline", "surface", GRAPHIC), ("chart-baseline", "surface-raised", GRAPHIC)]
    return out


def test_light_theme_blocks_are_identical() -> None:
    assert LIGHT_SYSTEM == LIGHT_MANUAL, "prefers-color-scheme ve [data-theme=light] aynı değerleri taşımalı"


def test_light_theme_only_overrides_existing_tokens() -> None:
    assert set(LIGHT_MANUAL) <= set(DARK), sorted(set(LIGHT_MANUAL) - set(DARK))


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_wcag_aa_contrast_pairs(theme: str) -> None:
    tokens = THEMES[theme]
    failures = []
    for foreground, background, needed in pairs(tokens):
        got = ratio(tokens[f"--{foreground}"], tokens[f"--{background}"])
        if got < needed:
            failures.append(f"{foreground} / {background}: {got:.2f} < {needed}")
    assert not failures, f"{theme}: " + "; ".join(failures)


def test_status_palette_keeps_distinct_semantics_and_consistent_themes() -> None:
    """Theme branding may change; five statuses and their fills remain distinct and consistent."""
    for tokens in THEMES.values():
        status_fills = [tokens[f"--{status}"].lower() for status in STATUSES]
        assert len(set(status_fills)) == len(STATUSES), "each status needs a distinct fill"
        assert all(re.fullmatch(r"#[0-9a-f]{6}", color) for color in status_fills)
        assert tokens["--on-accent"] != tokens["--accent"]
    for status in STATUSES:
        assert THEMES["light"][f"--{status}"] == DARK[f"--{status}"], "status fills stay consistent"


def test_unknown_is_never_green() -> None:
    for tokens in THEMES.values():
        assert tokens["--unknown"] != tokens["--ok"]
        assert tokens["--unknown-ink"] != tokens["--ok-ink"]
        assert tokens["--unknown-tint"] != tokens["--ok-tint"]


def test_spacing_tokens_are_a_monotonic_scale() -> None:
    values = [int(DARK[f"--space-{i}"].removesuffix("px")) for i in range(1, 9)]
    assert values == sorted(values) and len(set(values)) == 8


def test_style_has_no_raw_colors() -> None:
    """Renkler yalnızca tokens.css'te; bileşen kuralları HEX ya da rgb()/hsl() yazmaz."""
    without_urls = re.sub(r"url\([^)]*\)", "", STYLE)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", without_urls), "style.css içinde HEX renk var"
    assert not re.findall(r"\b(?:rgb|rgba|hsl|hsla|oklch|oklab)\(", without_urls)


def test_fonts_and_license_are_in_the_repo() -> None:
    files = re.findall(r'url\("(fonts/[^"]+\.woff2)"\)', STYLE)
    assert files, "yazı tipi dosyası bildirilmemiş"
    for file in files:
        font = FRONTEND / file
        assert font.is_file(), file
        assert font.read_bytes()[:4] == b"wOF2", f"{file} geçerli bir WOFF2 değil"
    license_text = (FRONTEND / "fonts" / "OFL-IBM-Plex.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "IBM Corp." in license_text


def test_no_third_party_requests_in_frontend_sources() -> None:
    for name in ("index.html", "style.css", "app.js", "mock.js", "css/tokens.css"):
        source = (FRONTEND / name).read_text(encoding="utf-8")
        hosts = re.findall(r"""(?:src|href|url)\s*[=(]\s*["']?(https?://[^"')\s]+)""", source)
        assert not hosts, f"{name}: harici kaynak {hosts}"


def test_motion_respects_reduced_motion_preference() -> None:
    assert "prefers-reduced-motion: reduce" in STYLE
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in script and "reducedMotion.matches" in script
