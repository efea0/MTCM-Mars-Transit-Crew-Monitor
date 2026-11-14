"""Branch önekine göre dosya sahipliği kontrolü (CI path-guard ve commit öncesi).

Kullanım: python tools/check_paths.py <branch-name> [<base-ref>]   (varsayılan base: origin/main)
"""

from __future__ import annotations

import subprocess
import sys

OWNERSHIP: dict[str, tuple[str, ...]] = {
    "baris/": (
        "backend/atmosphere.py",
        "backend/rules.py",
        "backend/simulator.py",
        "backend/comms.py",
        "backend/main.py",
        "backend/__init__.py",
        "tests/",
    ),
    "efe/": ("frontend/",),
    "enis/": ("data/", "docs/", "README.md", "reports/", "research_notes/"),
    "contract/": (
        "backend/models.py",
        "docs/contract.json",
        "requirements.txt",
        "tests/test_models.py",
    ),
    "infra/": (
        ".github/",
        "tools/",
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        ".gitignore",
        ".gitattributes",
    ),
}

EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "enis/": ("docs/contract.json",),
    "baris/": ("tests/test_models.py",),
}


def matches_rule(path: str, rule: str) -> bool:
    if rule.endswith("/"):
        return path.startswith(rule)
    return path == rule


def is_allowed(prefix: str, path: str) -> bool:
    if any(matches_rule(path, ex) for ex in EXCLUSIONS.get(prefix, ())):
        return False
    return any(matches_rule(path, rule) for rule in OWNERSHIP[prefix])


def find_owner(path: str) -> str:
    # Önce tam dosya eşleşmesi, sonra klasör öneki.
    for exact in (True, False):
        for prefix, rules in OWNERSHIP.items():
            if any(matches_rule(path, ex) for ex in EXCLUSIONS.get(prefix, ())):
                continue
            for rule in rules:
                if rule.endswith("/") != exact and matches_rule(path, rule):
                    return prefix
    return "sahipsiz (Efe'ye sor)"


def resolve_prefix(branch: str) -> str | None:
    for prefix in OWNERSHIP:
        if branch.startswith(prefix):
            return prefix
    return None


def get_changed_files(base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git diff çıkış kodu {result.returncode}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    branch = argv[1]
    base_ref = argv[2] if len(argv) == 3 else "origin/main"

    prefix = resolve_prefix(branch)
    if prefix is None:
        print(
            f"HATA: '{branch}' branch'inde çalışılmaz. İzinli önekler: {', '.join(OWNERSHIP)}",
            file=sys.stderr,
        )
        return 2

    try:
        changed = get_changed_files(base_ref)
    except RuntimeError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2

    offending = [p for p in changed if not is_allowed(prefix, p)]
    if offending:
        print(f"KAPSAM DIŞI: {prefix} branch'i başka rollerin dosyalarına dokunuyor:")
        for p in offending:
            print(f"  {p}  -> sahibi: {find_owner(p)}")
        return 1

    print(f"OK: {len(changed)} dosya {prefix} kapsamında")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
