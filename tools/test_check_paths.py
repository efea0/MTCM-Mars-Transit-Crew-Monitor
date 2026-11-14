import pytest

from tools import check_paths as cp


@pytest.mark.parametrize(
    "branch",
    ["feat/backend/spo2-rules", "fix/frontend/alert-list", "docs/delivery/readme", "chore/infra/ci"],
)
def test_valid_branch_names(branch):
    assert cp.BRANCH_RE.match(branch)


@pytest.mark.parametrize(
    "branch",
    [
        "baris/backend",
        "feat/spo2",
        "feat/api/spo2",
        "Feat/backend/x",
        "feat/backend/Spo2_Rules",
        "feat/backend/a/b",
        "hotfix/backend/x",
        "feat/backend/-x",
    ],
)
def test_invalid_branch_names(branch):
    assert not cp.BRANCH_RE.match(branch)


@pytest.mark.parametrize(
    ("area", "path", "ok"),
    [
        ("backend", "backend/rules.py", True),
        ("backend", "backend/models.py", False),
        ("backend", "tests/test_rules.py", True),
        ("backend", "tests/test_models.py", False),
        ("backend", "requirements.txt", True),
        ("contract", "backend/models.py", True),
        ("contract", "docs/contract.json", True),
        ("delivery", "docs/contract.json", False),
        ("delivery", "docs/sources.md", True),
        ("delivery", "README.md", True),
        ("frontend", "frontend/app.js", True),
        ("frontend", "backend/main.py", False),
        ("infra", ".github/workflows/path-guard.yml", True),
        ("infra", "Makefile", True),  # sahipsiz kök dosya -> infra
        ("infra", "README.md", False),
        ("data", "data/donki.json", True),
    ],
)
def test_area_scope(area, path, ok):
    assert cp.in_area(area, path) is ok


def test_owner_lookup():
    assert cp.owner_of("docs/contract.json") == "contract"
    assert cp.owner_of("tests/test_models.py") == "contract"
    assert cp.owner_of("docs/x.md") == "delivery"
    assert cp.owner_of("Makefile") == "infra"


def test_main_accepts_only_develop():
    assert cp.check("develop", "main", None, "origin/main") == []
    assert cp.check("feat/backend/x", "main", None, "origin/main")


def test_title_must_match_branch(monkeypatch):
    monkeypatch.setattr(cp, "changed_files", lambda _ref: ["backend/rules.py"])
    assert cp.check("feat/backend/spo2", "develop", "feat(backend): SpO2 kuralı", "x") == []
    assert cp.check("feat/backend/spo2", "develop", "fix(backend): SpO2 kuralı", "x")
    assert cp.check("feat/backend/spo2", "develop", "SpO2 kuralı ekle", "x")


def test_out_of_area_file_rejected(monkeypatch):
    monkeypatch.setattr(cp, "changed_files", lambda _ref: ["backend/rules.py", "frontend/app.js"])
    errors = cp.check("feat/backend/spo2", "develop", None, "x")
    assert any("frontend/app.js" in e for e in errors)


def test_feature_freeze(monkeypatch):
    monkeypatch.setattr(cp, "changed_files", lambda _ref: ["backend/rules.py"])
    monkeypatch.setenv("FEATURE_FREEZE", "true")
    assert cp.check("feat/backend/spo2", "develop", None, "x")
    assert cp.check("fix/backend/spo2", "develop", None, "x") == []
