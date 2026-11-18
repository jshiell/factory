import stat

import pytest

from factory.gate import commit_all
from factory.worktrees import WorktreeManager, run_git
from gitutil import git


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


@pytest.fixture
def user_filter(tmp_path, monkeypatch):
    """Defines a clean/smudge filter in the user's global git config (as git-lfs would); returns the canary directory the filter writes to."""
    canaries = tmp_path / "canaries"
    canaries.mkdir()
    for name in ("clean", "smudge"):
        script = tmp_path / f"{name}.sh"
        script.write_text(f"#!/bin/sh\necho x >> {canaries}/{name}\ncat\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    config = tmp_path / "user-gitconfig"
    config.write_text(f"[filter \"probe\"]\n\tclean = {tmp_path}/clean.sh\n\tsmudge = {tmp_path}/smudge.sh\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    return canaries


def test_creating_a_worktree_still_applies_the_users_smudge_filter_to_the_committed_tree(repo, tmp_path, user_filter, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")  # commit the fixture without the filter
    (repo / ".gitattributes").write_text("*.probe filter=probe\n")
    (repo / "big.probe").write_text("content")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "attrs")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "user-gitconfig"))

    WorktreeManager(repo, tmp_path / "wt").create("t1")

    assert (user_filter / "smudge").exists()


@pytest.mark.parametrize(("user_config", "filter_runs"), [(False, False), (True, True)])
def test_the_factorys_git_ignores_the_users_filter_unless_asked_to_use_it(repo, user_filter, user_config, filter_runs):
    (repo / ".gitattributes").write_text("*.probe filter=probe\n")
    (repo / "agent.probe").write_text("from the agent")

    run_git(repo, "add", "-A", user_config=user_config)

    assert (user_filter / "clean").exists() is filter_runs


def test_an_injected_git_config_environment_does_not_run_a_filter(repo, tmp_path, user_filter, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.probe.clean")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "clean.sh"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    (repo / ".gitattributes").write_text("*.probe filter=probe\n")
    (repo / "agent.probe").write_text("from the agent")

    run_git(repo, "add", "-A")

    assert not (user_filter / "clean").exists()


def test_merging_into_the_main_checkout_does_not_run_the_users_smudge_filter(repo, tmp_path, user_filter, monkeypatch):
    from factory.merge_task import merge_task
    from factory.store import TaskStore
    from factory.task import Task, TaskState

    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    manager = WorktreeManager(repo, tmp_path / "wt")
    worktree, base = manager.create("t1")
    (worktree / ".gitattributes").write_text("*.probe filter=probe\n")
    (worktree / "agent.probe").write_text("from the agent")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t1")
    tip = git(worktree, "rev-parse", "HEAD")
    store.save(Task(id="t1", prompt="p", state=TaskState.AUDITED, worktree=str(worktree), base_sha=base, audited_sha=tip))
    for canary in user_filter.iterdir():
        canary.unlink()

    merge_task("t1", store, repo, manager, shown_sha=tip, main_branch="main")

    assert not (user_filter / "smudge").exists()


def test_the_hardened_prefix_is_only_used_by_the_module_that_adds_the_hardened_environment():
    from pathlib import Path
    source = Path(__file__).parent.parent / "src" / "factory"
    outside = [path.name for path in source.rglob("*.py") if path.name != "worktrees.py" and "HARDENED_GIT" in path.read_text()]

    assert outside == []


@pytest.mark.parametrize(("user_config", "filter_runs"), [(False, False), (True, True)])
def test_a_filter_in_the_xdg_git_config_is_ignored_too(repo, tmp_path, user_filter, monkeypatch, user_config, filter_runs):
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "config").write_text((tmp_path / "user-gitconfig").read_text())
    monkeypatch.delenv("GIT_CONFIG_GLOBAL")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (repo / ".gitattributes").write_text("*.probe filter=probe\n")
    (repo / "agent.probe").write_text("from the agent")

    run_git(repo, "add", "-A", user_config=user_config)

    assert (user_filter / "clean").exists() is filter_runs


def test_the_gates_commit_and_the_status_after_a_same_size_edit_run_no_user_filter(repo, user_filter, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    (repo / ".gitattributes").write_text("*.probe filter=probe\n")
    (repo / "agent.probe").write_text("hello")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "attrs")
    (repo / "agent.probe").write_text("jello")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(user_filter.parent / "user-gitconfig"))

    run_git(repo, "status", "--porcelain")
    commit_all(repo, "Task t1")

    assert not (user_filter / "clean").exists()
