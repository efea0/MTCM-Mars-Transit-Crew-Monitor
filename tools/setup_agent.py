"""Tek seferlik kurulum: rolü kaydeder ve yerel git kancalarını açar.

Kullanım:  python tools/setup_agent.py aydin|baris|enis
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from check_paths import ROLE_AREAS, ROLE_FILE

NAMES = {"aydin": "Aydın", "baris": "Barış", "enis": "Enis"}
ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1].lower() not in ROLE_AREAS:
        print(__doc__, file=sys.stderr)
        return 2
    role = argv[1].lower()
    areas = ", ".join(ROLE_AREAS[role])
    (ROOT / ROLE_FILE).write_text(
        f"{role}\n# {NAMES[role]} — alanlar: {areas}. Bu dosya yereldir, repoya girmez.\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=ROOT, check=True)
    print(f"Rol kaydedildi: {NAMES[role]} ({areas})")
    print("Git kancaları açık: main/develop'a commit, alan dışı dosya ve ruff hatası commit'te engellenir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
