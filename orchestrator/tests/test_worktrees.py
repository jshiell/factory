import subprocess
from pathlib import Path

import pytest

from factory.worktrees import WorktreeManager


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def manager(repo, tmp_path):
    return WorktreeManager(repo, tmp_path / "worktrees")


def test_create_makes_a_branch_worktree_at_the_current_head(repo, manager):
    path, base_sha = manager.create("t1")

    assert base_sha == git(repo, "rev-parse", "HEAD")
    assert git(path, "rev-parse", "HEAD") == base_sha
    assert git(path, "rev-parse", "--abbrev-ref", "HEAD") == "factory/t1"
