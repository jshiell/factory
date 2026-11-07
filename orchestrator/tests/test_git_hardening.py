import stat

import pytest

from gitutil import git

from factory.gate import commit_all
from factory.worktrees import run_git


@pytest.fixture
def planted(repo, tmp_path):
    """A repo whose `target/x` is a nested repo with a core.fsmonitor hook that writes a marker when run."""
    marker = tmp_path / "marker"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    nested = repo / "target" / "x"
    nested.mkdir(parents=True)
    git(nested, "init", "-q", "-b", "main")
    (nested / "f.txt").write_text("f")
    git(nested, "add", "-A")
    git(nested, "commit", "-q", "-m", "n")
    git(nested, "config", "core.fsmonitor", str(hook))
    return repo, marker


def test_the_factorys_git_does_not_run_a_planted_fsmonitor_hook(planted):
    repo, marker = planted

    commit_all(repo, "Task t1")
    run_git(repo, "status", "--porcelain")

    assert not marker.exists()


def test_every_git_call_over_a_worktree_goes_through_the_hardened_prefix():
    from pathlib import Path
    source = Path(__file__).parent.parent / "src" / "factory"
    bare = [path.name for path in source.rglob("*.py")
            if path.name not in ("worktrees.py", "settings.py") and '["git"' in path.read_text()]

    assert bare == []
