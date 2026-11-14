"""Branch adı, PR başlığı ve dosya kapsamı politikası (CI `scope` işi ve commit öncesi).

Kullanım:
    python tools/check_paths.py <branch> [--base develop|main] [--title "<PR başlığı>"]

Kurallar:
- İş branch'i: <tür>/<alan>/<kısa-açıklama>, örn. feat/backend/spo2-rules
- Hedef `develop`: dosyalar yalnızca branch'in alanında olabilir.
- Hedef `main`: yalnızca `develop` (release PR'ı) kabul edilir.
- FEATURE_FREEZE=true ortam değişkeni varsa feat/ ve refactor/ reddedilir.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

TYPES = ("feat", "fix", "refactor", "test", "docs", "chore")
FROZEN_TYPES = ("feat", "refactor")
SLUG_MAX = 40

# alan -> (izinli yollar, hariç yollar). "/" ile biten = klasör öneki, diğerleri tam yol.
# Sahipler: backend/frontend/contract/infra -> Aydın · comms/qa -> Barış · reference/delivery -> Enis
AREAS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "backend": (
        ("backend/", "tests/", "requirements.txt"),
        (
            "backend/models.py",
            "backend/comms.py",
            "tests/test_models.py",
            "tests/test_comms.py",
            "tests/conftest.py",
        ),
    ),
    "frontend": (("frontend/",), ()),
    "comms": (("backend/comms.py", "tests/test_comms.py"), ()),
    "qa": (("tests/", "data/scenarios/", "data/donki/"), ("tests/test_models.py", "tests/conftest.py")),
    "reference": (("data/reference/",), ()),
    "delivery": (
        ("docs/", "README.md", "reports/", "research_notes/"),
        ("docs/contract.json",),
    ),
    "contract": (
        ("backend/models.py", "docs/contract.json", "tests/test_models.py", "tests/conftest.py"),
        (),
    ),
    "infra": (
        (
            ".github/",
            ".githooks/",
            ".claude/settings.json",
            "tools/",
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
            "pyproject.toml",
            ".gitignore",
            ".gitattributes",
        ),
        (),
    ),
}

# Kişi -> çalışabileceği alanlar. Yerelde .agent-role dosyasından okunur (CI'de yok).
ROLE_AREAS: dict[str, tuple[str, ...]] = {
    "aydin": ("backend", "frontend", "contract", "infra"),
    "baris": ("qa", "comms"),
    "enis": ("reference", "delivery"),
}
ROLE_FILE = ".agent-role"

BRANCH_RE = re.compile(
    rf"^(?P<type>{'|'.join(TYPES)})/(?P<area>{'|'.join(AREAS)})/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)
TITLE_RE = re.compile(rf"^(?P<type>{'|'.join(TYPES)})\((?P<area>{'|'.join(AREAS)})\): \S.{{2,}}$")


def _match(path: str, rule: str) -> bool:
    return path.startswith(rule) if rule.endswith("/") else path == rule


def _explicitly_in(area: str, path: str) -> bool:
    allowed, excluded = AREAS[area]
    if any(_match(path, r) for r in excluded):
        return False
    return any(_match(path, r) for r in allowed)


def owners_of(path: str) -> list[str]:
    """Dosyaya dokunabilen alanlar. Hiçbir alana ait olmayan kök dosyalar altyapıya düşer."""
    owners = [a for a in AREAS if _explicitly_in(a, path)]
    if not owners and "/" not in path:
        owners = ["infra"]
    return owners


def in_area(area: str, path: str) -> bool:
    return area in owners_of(path)


def changed_files(base_ref: str, head_ref: str = "HEAD") -> list[str]:
    """Değişen dosyalar. Yeniden adlandırmada hem eski hem yeni yol döner; Türkçe adlar tırnaklanmaz."""
    res = subprocess.run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            f"{base_ref}...{head_ref}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"git diff çıkış kodu {res.returncode}")
    return [p for p in res.stdout.split("\0") if p]


def staged_files() -> list[str]:
    """Commit'e hazırlanmış (git add) dosyalar."""
    res = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--no-renames", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"git diff çıkış kodu {res.returncode}")
    return [p for p in res.stdout.split("\0") if p]


def read_role(path: str = ROLE_FILE) -> str | None:
    """.agent-role dosyasındaki ilk kelime (aydin | baris | enis); dosya yoksa None."""
    try:
        with open(path, encoding="utf-8") as fh:
            words = fh.read().split()
    except FileNotFoundError:
        return None
    return words[0].lower() if words else None


def check(
    branch: str,
    base: str,
    title: str | None,
    base_ref: str,
    head_ref: str = "HEAD",
    same_repo: bool = True,
    role: str | None = None,
    staged: bool = False,
) -> list[str]:
    errors: list[str] = []
    if role is not None and role not in ROLE_AREAS:
        return [f"Geçersiz rol '{role}' ({ROLE_FILE}). Seçenekler: {', '.join(ROLE_AREAS)}"]

    if not same_repo:
        return ["Fork'tan gelen PR kabul edilmez; branch'i ana repoda aç."]
    if base == "main":
        if branch != "develop":
            errors.append(f"main'e yalnızca develop'tan release PR'ı açılır (gelen: '{branch}').")
        return errors
    if base != "develop":
        return [f"PR hedefi develop olmalı (gelen: '{base}')."]

    m = BRANCH_RE.match(branch)
    if not m:
        return [
            f"Branch adı geçersiz: '{branch}'. Biçim: <tür>/<alan>/<kısa-açıklama>, "
            f"tür={'|'.join(TYPES)}, alan={'|'.join(AREAS)}, açıklama küçük harf ve tire. "
            "Örnek: feat/backend/spo2-rules"
        ]
    btype, area, slug = m["type"], m["area"], m["slug"]
    if role is not None and area not in ROLE_AREAS[role]:
        errors.append(
            f"'{area}' alanı '{role}' rolüne ait değil. Senin alanların: {', '.join(ROLE_AREAS[role])}"
        )
    if len(slug) > SLUG_MAX:
        errors.append(f"Kısa açıklama en fazla {SLUG_MAX} karakter olmalı ({len(slug)}).")
    if os.environ.get("FEATURE_FREEZE", "").lower() == "true" and btype in FROZEN_TYPES:
        errors.append(f"Özellik kilidi aktif: '{btype}/' PR'ı kabul edilmiyor; yalnızca fix/test/docs/chore.")

    if title is not None:
        t = TITLE_RE.match(title)
        if not t:
            errors.append(f"PR başlığı geçersiz: '{title}'. Biçim: {btype}({area}): kısa özet")
        elif (t["type"], t["area"]) != (btype, area):
            errors.append(
                f"PR başlığı branch ile uyuşmuyor: başlık {t['type']}({t['area']}), branch {btype}/{area}."
            )

    try:
        files = staged_files() if staged else changed_files(base_ref, head_ref)
    except RuntimeError as exc:
        return errors + [f"git diff başarısız: {exc}"]
    outside = [p for p in files if not in_area(area, p)]
    for p in outside:
        errors.append(f"Alan dışı dosya: {p} (izinli alan: {', '.join(owners_of(p)) or 'yok'})")
    if not errors:
        print(f"OK: {branch} -> {base}, {len(files)} dosya '{area}' alanında")
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("branch")
    p.add_argument("--base", default="develop", help="PR hedefi: develop veya main")
    p.add_argument("--base-ref", default=None, help="Karşılaştırma ref'i (varsayılan origin/<base>)")
    p.add_argument("--head-ref", default="HEAD", help="PR'ın son commit'i (varsayılan HEAD)")
    p.add_argument("--title", default=None)
    p.add_argument("--head-repo", default=None, help="PR'ın geldiği repo (owner/name)")
    p.add_argument("--repo", default=None, help="Ana repo (owner/name)")
    p.add_argument("--staged", action="store_true", help="Yalnızca commit'e hazırlanan dosyaları denetle")
    p.add_argument("--role", default=None, help=f"aydin|baris|enis (varsayılan: {ROLE_FILE} dosyası)")
    a = p.parse_args(argv)

    same_repo = a.head_repo is None or a.repo is None or a.head_repo == a.repo
    errors = check(
        a.branch,
        a.base,
        a.title,
        a.base_ref or f"origin/{a.base}",
        a.head_ref,
        same_repo=same_repo,
        role=a.role or read_role(),
        staged=a.staged,
    )
    for e in errors:
        print(f"HATA: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
