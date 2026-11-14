import subprocess

import pytest

from tools import check_paths as cp


def _git(cwd, *args):
    """Test deposusunda git: GIT_* ortam değişkenleri temizlenir, yalnızca verilen cwd'de çalışır."""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=cp.clean_git_env())


@pytest.mark.parametrize(
    "branch",
    [
        "feat/backend/spo2-rules",
        "fix/frontend/alert-list",
        "docs/delivery/readme",
        "chore/infra/ci",
        "test/qa/missing-data",
        "feat/comms/light-delay",
        "docs/reference/pio2-table",
    ],
)
def test_valid_branch_names(branch):
    assert cp.BRANCH_RE.match(branch)


@pytest.mark.parametrize(
    "branch",
    [
        "baris/backend",
        "feat/spo2",
        "feat/api/spo2",
        "feat/data/x",
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
        ("backend", "backend/comms.py", False),
        ("comms", "backend/comms.py", True),
        ("comms", "backend/rules.py", False),
        ("qa", "tests/test_rules.py", True),
        ("qa", "tests/test_models.py", False),
        ("qa", "data/scenarios/o2-failure.json", True),
        ("qa", "data/donki/sep_event.json", True),
        ("qa", "backend/rules.py", False),
        ("reference", "data/reference/ranges.json", True),
        ("reference", "data/scenarios/x.json", False),
    ],
)
def test_area_scope(area, path, ok):
    assert cp.in_area(area, path) is ok


def test_owner_lookup():
    assert cp.owners_of("docs/contract.json") == ["contract"]
    assert cp.owners_of("tests/test_models.py") == ["contract"]
    assert cp.owners_of("tests/test_comms.py") == ["comms", "qa"]
    assert cp.owners_of("tests/test_rules.py") == ["backend", "qa"]
    assert cp.owners_of("docs/x.md") == ["delivery"]
    assert cp.owners_of("Makefile") == ["infra"]
    assert cp.owners_of("data/random.json") == []


def test_main_accepts_only_develop():
    assert cp.check("develop", "main", None, "origin/main") == []
    assert cp.check("feat/backend/x", "main", None, "origin/main")


def test_title_must_match_branch(monkeypatch):
    monkeypatch.setattr(cp, "changed_files", lambda *_: ["backend/rules.py"])
    assert cp.check("feat/backend/spo2", "develop", "feat(backend): SpO2 kuralı", "x") == []
    assert cp.check("feat/backend/spo2", "develop", "fix(backend): SpO2 kuralı", "x")
    assert cp.check("feat/backend/spo2", "develop", "SpO2 kuralı ekle", "x")


def test_out_of_area_file_rejected(monkeypatch):
    monkeypatch.setattr(cp, "changed_files", lambda *_: ["backend/rules.py", "frontend/app.js"])
    errors = cp.check("feat/backend/spo2", "develop", None, "x")
    assert any("frontend/app.js" in e for e in errors)


def test_feature_freeze(monkeypatch):
    monkeypatch.setattr(cp, "changed_files", lambda *_: ["backend/rules.py"])
    monkeypatch.setenv("FEATURE_FREEZE", "true")
    assert cp.check("feat/backend/spo2", "develop", None, "x")
    assert cp.check("fix/backend/spo2", "develop", None, "x") == []


def test_conftest_belongs_to_contract():
    assert cp.owners_of("tests/conftest.py") == ["contract"]


def test_fork_rejected():
    assert cp.check("develop", "main", None, "x", same_repo=False)


def test_renames_and_unicode_paths(tmp_path, monkeypatch):
    def git(*args):
        _git(tmp_path, *args)

    git("init", "-q", "-b", "develop")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "models.py").write_text("x", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "base")
    git("switch", "-qc", "feat/backend/x")
    git("mv", "backend/models.py", "backend/schemas.py")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "görev planı.md").write_text("x", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "move")
    monkeypatch.chdir(tmp_path)
    files = cp.changed_files("develop")
    assert "backend/models.py" in files and "backend/schemas.py" in files
    assert "reports/görev planı.md" in files


def test_git_env_leak_does_not_reach_temp_repo(tmp_path, monkeypatch):
    # Kanca ortamını taklit et: GIT_DIR bir "gerçek" depoyu gösteriyormuş gibi, ama bu yol
    # geçici dizinin içinde ve var olmayan bir yol. Sızıntı varsa git init bu yolu oluşturur.
    decoy = tmp_path / "decoy-real-repo.git"
    monkeypatch.setenv("GIT_DIR", str(decoy))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / "index"))
    assert not any(k.startswith("GIT_") for k in cp.clean_git_env())

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "develop")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "backend").mkdir()
    (repo / "backend" / "rules.py").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "switch", "-qc", "feat/backend/x")
    (repo / "backend" / "rules2.py").write_text("y", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")

    monkeypatch.chdir(repo)
    assert cp.changed_files("develop") == ["backend/rules2.py"]
    assert not decoy.exists()
    assert (repo / ".git").is_dir()


def test_role_limits_areas(monkeypatch):
    monkeypatch.setattr(cp, "changed_files", lambda *_: ["tests/test_rules.py"])
    assert cp.check("test/qa/rules", "develop", None, "x", role="baris") == []
    errors = cp.check("feat/backend/rules", "develop", None, "x", role="baris")
    assert any("rolüne ait değil" in e for e in errors)
    assert cp.check("feat/backend/x", "develop", None, "x", role="nobody")


def test_baris_may_use_frontend_area(monkeypatch):
    monkeypatch.setattr(cp, "changed_files", lambda *_: ["frontend/app.js"])
    assert cp.check("feat/frontend/cards", "develop", None, "x", role="baris") == []
    assert cp.check("feat/frontend/cards", "develop", None, "x", role="enis")


def test_read_role(tmp_path):
    f = tmp_path / ".agent-role"
    assert cp.read_role(str(f)) is None
    f.write_text("baris\n# not\n", encoding="utf-8")
    assert cp.read_role(str(f)) == "baris"
