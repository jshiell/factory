import pytest

from gitutil import git

from factory.audit_corpus import BASE_FILES, CASES, PROJECT_SUBDIR
from factory.audit_eval import build_base_repo, materialise
from factory.worktrees import WorktreeManager


@pytest.fixture(autouse=True)
def no_global_git_config(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def test_the_base_repo_holds_the_inlined_project_under_the_project_subdir_in_one_commit(tmp_path):
    repo = build_base_repo(tmp_path)

    for rel, content in BASE_FILES.items():
        assert (repo / PROJECT_SUBDIR / rel).read_text() == content
    assert git(repo, "rev-list", "--count", "HEAD") == "1"


def test_materialise_commits_the_after_state_on_a_fresh_worktree_with_neutral_names(tmp_path):
    case = next(c for c in CASES if c.id == "case-01")
    manager = WorktreeManager(build_base_repo(tmp_path), tmp_path / "wt")

    worktree, base_sha = materialise(case, manager, "a1-case-01")

    assert git(worktree, "rev-parse", "HEAD") != base_sha and git(worktree, "status", "--porcelain") == ""
    assert "def subtract" in (worktree / PROJECT_SUBDIR / "src/calc/__init__.py").read_text()
    assert git(worktree, "log", "-1", "--format=%s|%an") == "change|factory"
    assert case.label not in git(worktree, "log", "--format=%s%b%an") and case.category not in git(worktree, "branch", "--list")
