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
            if path.name not in ("worktrees.py", "settings.py", "clone_repo.py", "github.py") and '["git"' in path.read_text()]

    assert bare == []


def test_the_audit_diff_does_not_run_a_textconv_driver_selected_by_a_gitattributes_in_the_project(repo, tmp_path):
    import json

    from factory.audit_task import audit_task
    from factory.command import RunnerConfig
    from factory.ledger import Ledger
    from factory.step import RunResult
    from factory.store import TaskStore
    from factory.task import Task, TaskState

    marker = tmp_path / "textconv-ran"
    script = tmp_path / "textconv.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat \"$1\"\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    git(repo, "config", "diff.evil.textconv", str(script))  # stands in for a driver from the user's gitconfig
    (repo / "proj").mkdir()
    (repo / "proj" / "a.txt").write_text("1")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "proj" / ".gitattributes").write_text("*.txt diff=evil\n")
    (repo / "proj" / "a.txt").write_text("2")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    store.save(Task(id="t1", prompt="p", state=TaskState.VERIFIED, worktree=str(repo), base_sha=base))
    verdict = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                          "structured_output": {"verdict": "pass", "findings": []}})

    class Runner:
        def run(self, task, config):
            return RunResult(verdict, 0)

    audit_task("t1", store, Ledger(tmp_path / "l.jsonl"), Runner(), RunnerConfig(model="m", budget_usd=0.3), 5.0, "proj")

    assert not marker.exists()
