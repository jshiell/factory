import json
from dataclasses import replace
from pathlib import Path

import pytest

from gitutil import git

from factory.step import RunResult
from factory.rebase_task import RebaseRefused, rebase_task
from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.worktrees import WorktreeManager


@pytest.fixture
def audited(tmp_path, repo):
    """An audited task t1 (b.txt added) whose base main has since moved past (m.txt added on main)."""
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    manager = WorktreeManager(repo, tmp_path / "wt")
    worktree, base = manager.create("t1")
    (worktree / "b.txt").write_text("b")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t1")
    tip = git(worktree, "rev-parse", "HEAD")
    task = Task(id="t1", prompt="p", state=TaskState.AUDITED, worktree=str(worktree), base_sha=base, goal="g", plan="pl",
                audited_sha=tip, audit_findings=("fine",), audit_error="old", fix_rounds_used=2, fix_frozen_sha=base)
    store.save(task)
    (repo / "m.txt").write_text("m")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved")
    return store, repo, worktree, task


def test_a_clean_rebase_replays_the_task_on_main_and_sends_it_back_to_agent_done(audited):
    store, repo, worktree, before = audited
    main_head = git(repo, "rev-parse", "HEAD")

    result = rebase_task("t1", store, repo)

    after = store.load("t1")
    assert after.state is TaskState.AGENT_DONE and after.base_sha == main_head
    assert git(worktree, "rev-parse", "HEAD") == git(repo, "rev-parse", "factory/t1") != before.audited_sha
    assert git(worktree, "merge-base", "--is-ancestor", main_head, "HEAD") == ""
    assert (worktree / "b.txt").exists() and (worktree / "m.txt").exists()
    assert (after.audited_sha, after.audit_findings, after.audit_error) == (None, (), None)
    assert (after.fix_rounds_used, after.fix_in_flight, after.fix_frozen_sha) == (0, False, None)
    assert replace(after, state=before.state, base_sha=before.base_sha, audited_sha=before.audited_sha,
                   audit_findings=before.audit_findings, audit_error=before.audit_error,
                   fix_rounds_used=before.fix_rounds_used, fix_frozen_sha=before.fix_frozen_sha) == before
    assert result.old_audited_sha == before.audited_sha


def break_dirty(store, repo, worktree, task):
    (worktree / "b.txt").write_text("changed")


def break_worktree_head(store, repo, worktree, task):
    (worktree / "c.txt").write_text("c")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "extra")


def break_detached(store, repo, worktree, task):
    git(worktree, "checkout", "-q", "--detach")


def break_main_not_moved(store, repo, worktree, task):
    git(repo, "reset", "-q", "--hard", task.base_sha)


def break_already_in_main(store, repo, worktree, task):
    git(repo, "merge", "-q", "--no-edit", task.audited_sha)


def break_fix_in_flight(store, repo, worktree, task):
    store.save(replace(task, fix_in_flight=True))


def break_no_worktree(store, repo, worktree, task):
    store.save(replace(task, worktree=None))


def break_worktree_dir_missing(store, repo, worktree, task):
    store.save(replace(task, worktree=str(worktree) + "-gone"))


REFUSALS = [
    (break_dirty, "dirty"), (break_worktree_head, "audited commit"), (break_detached, "factory/t1"),
    (break_main_not_moved, "has not moved"), (break_already_in_main, "already in main"),
    (break_fix_in_flight, "advance"), (break_no_worktree, "worktree"), (break_worktree_dir_missing, "worktree"),
]


@pytest.mark.parametrize("break_it, message", REFUSALS, ids=[fn.__name__ for fn, _ in REFUSALS])
def test_a_failed_precondition_refuses_and_changes_nothing(audited, break_it, message):
    store, repo, worktree, task = audited
    break_it(store, repo, worktree, task)
    stored_before = store.load("t1")
    heads_before = (git(worktree, "rev-parse", "HEAD"), git(repo, "rev-parse", "factory/t1"), git(repo, "rev-parse", "HEAD"))

    with pytest.raises(RebaseRefused, match=message):
        rebase_task("t1", store, repo)

    assert store.load("t1") == stored_before
    assert (git(worktree, "rev-parse", "HEAD"), git(repo, "rev-parse", "factory/t1"), git(repo, "rev-parse", "HEAD")) == heads_before


def test_a_task_that_is_not_audited_is_refused_by_the_state_machine(audited):
    from factory.task import InvalidTransition
    store, repo, _, task = audited
    store.save(replace(task, state=TaskState.VERIFIED))

    with pytest.raises(InvalidTransition):
        rebase_task("t1", store, repo)


def conflict_on_main(repo):
    (repo / "b.txt").write_text("main's b")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main also adds b")


def in_progress(worktree):
    return any((worktree / git(worktree, "rev-parse", "--git-path", name)).exists() for name in ("rebase-merge", "rebase-apply"))


def test_a_conflict_is_refused_the_rebase_aborted_and_the_old_state_left_intact(audited):
    store, repo, worktree, task = audited
    conflict_on_main(repo)
    stored_before = store.load("t1")

    with pytest.raises(RebaseRefused, match="conflict.*abandon.*retry"):
        rebase_task("t1", store, repo)

    assert store.load("t1") == stored_before
    assert git(worktree, "rev-parse", "HEAD") == git(repo, "rev-parse", "factory/t1") == task.audited_sha
    assert git(worktree, "status", "--porcelain") == "" and not in_progress(worktree)


def test_an_interrupt_during_the_rebase_aborts_it_restores_the_old_tip_and_propagates(audited, monkeypatch):
    import subprocess
    from factory import rebase_task as module
    store, repo, worktree, task = audited
    conflict_on_main(repo)
    real = module.run_git

    def interrupted_mid_rebase(cwd, *args, **kwargs):
        if "--onto" in args:
            with pytest.raises(subprocess.CalledProcessError):
                real(cwd, *args, **kwargs)  # leaves the rebase stopped on the conflict, as a signal would
            raise KeyboardInterrupt
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(module, "run_git", interrupted_mid_rebase)

    with pytest.raises(KeyboardInterrupt):
        rebase_task("t1", store, repo)

    assert git(worktree, "rev-parse", "HEAD") == git(repo, "rev-parse", "factory/t1") == task.audited_sha
    assert git(worktree, "status", "--porcelain") == "" and not in_progress(worktree)
    assert store.load("t1") == task


def test_guard_a_failure_before_the_rebase_starts_reports_gits_reason_without_an_abort_alarm(audited):
    store, repo, worktree, task = audited
    store.save(replace(task, base_sha="0" * 40))

    with pytest.raises(RebaseRefused) as raised:
        rebase_task("t1", store, repo)

    assert "could not be restored" not in str(raised.value) and "git rebase failed" in str(raised.value)


def test_a_redirected_dot_git_file_is_refused_before_any_git_runs_in_the_worktree(audited, tmp_path):
    store, repo, worktree, task = audited
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    (worktree / ".git").write_text(f"gitdir: {other}/.git\n")

    with pytest.raises(RebaseRefused, match="gitdir"):
        rebase_task("t1", store, repo)

    assert store.load("t1") == task


def test_a_dot_git_directory_in_the_worktree_is_refused(audited):
    store, repo, worktree, task = audited
    (worktree / ".git").unlink()
    (worktree / ".git").mkdir()

    with pytest.raises(RebaseRefused, match="gitdir"):
        rebase_task("t1", store, repo)


def test_the_users_global_rebase_update_refs_does_not_move_other_branches(audited, tmp_path, monkeypatch):
    store, repo, worktree, task = audited
    (worktree / "c.txt").write_text("c")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "second")
    tip = git(worktree, "rev-parse", "HEAD")
    store.save(replace(task, audited_sha=tip))
    git(repo, "branch", "side", task.audited_sha)  # stacked on the first task commit
    glob = tmp_path / "global.gitconfig"
    glob.write_text("[rebase]\n\tupdateRefs = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(glob))

    rebase_task("t1", store, repo)

    assert git(repo, "rev-parse", "side") == task.audited_sha


def test_a_filter_from_the_users_global_config_does_not_run_during_the_rebase(audited, tmp_path, monkeypatch):
    store, repo, worktree, task = audited
    marker = tmp_path / "filter-ran"
    (repo / ".gitattributes").write_text("m.txt filter=mark\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "attributes")
    smudge = tmp_path / "smudge.sh"
    smudge.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    smudge.chmod(0o755)
    glob = tmp_path / "global.gitconfig"
    glob.write_text(f"[filter \"mark\"]\n\tsmudge = {smudge}\n\tclean = cat\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(glob))

    rebase_task("t1", store, repo)

    assert not marker.exists()


def test_guard_signing_and_hooks_configured_in_the_repo_do_not_get_in_the_way(audited, tmp_path):
    store, repo, worktree, task = audited
    marker = tmp_path / "hook-ran"
    git(repo, "config", "commit.gpgsign", "true")
    git(repo, "config", "gpg.program", "false")  # signing fails for certain unless switched off
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git(repo, "config", "core.hooksPath", str(hooks))
    for name in ("pre-rebase", "post-checkout", "post-commit", "post-rewrite", "prepare-commit-msg"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / name).chmod(0o755)

    rebase_task("t1", store, repo)

    assert store.load("t1").state is TaskState.AGENT_DONE and not marker.exists()


def test_guard_accepted_residual_a_merge_union_attribute_resolves_a_conflict_silently(audited):
    store, repo, worktree, task = audited
    (repo / ".gitattributes").write_text("b.txt merge=union\n")
    (repo / "b.txt").write_text("main's b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "union attributes and a clashing b")

    rebase_task("t1", store, repo)  # re-gate and re-audit are what judge the concatenated result

    assert store.load("t1").state is TaskState.AGENT_DONE


# --- wiring: a rebased task must pass the real gate and audit again before main will take it -----------

@pytest.fixture
def audited_with_fix_history(tmp_path, repo):
    """Audited t1 in project `proj`: two task commits, a fix round that froze the first, then main moves."""
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "project")
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    worktree, base = WorktreeManager(repo, tmp_path / "wt").create("t1")
    (worktree / "proj" / "code.py").write_text("y")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "first")
    frozen = git(worktree, "rev-parse", "HEAD")
    (worktree / "proj" / "more.py").write_text("z")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "fix round")
    tip = git(worktree, "rev-parse", "HEAD")
    store.save(Task(id="t1", prompt="make y", state=TaskState.AUDITED, worktree=str(worktree), base_sha=base,
                    goal="g", audited_sha=tip, audit_findings=("fine",), fix_rounds_used=1, fix_frozen_sha=frozen))
    (repo / "m.txt").write_text("m")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved")
    return store, repo, tip, tmp_path


class PassingAudit:
    def run(self, task, config):
        payload = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                   "structured_output": {"verdict": "pass", "findings": ["still fine"]}}
        return RunResult(json.dumps(payload), 0, False)


def test_a_rebased_task_is_unmergeable_until_the_gate_and_audit_pass_again_and_then_merges(audited_with_fix_history):
    from factory.audit_task import AUDIT_CONFIG, audit_task
    from factory.ledger import Ledger
    from factory.merge_task import MergeRefused, merge_preview, merge_task
    from factory.task import InvalidTransition
    from factory.verify_task import verify_task
    store, repo, old_tip, tmp_path = audited_with_fix_history
    manager = WorktreeManager(repo, tmp_path / "wt")

    rebase_task("t1", store, repo)

    new_head = git(Path(store.load("t1").worktree), "rev-parse", "HEAD")
    with pytest.raises((MergeRefused, InvalidTransition)):
        merge_preview("t1", store, repo)
    for shown in (old_tip, new_head):
        with pytest.raises((MergeRefused, InvalidTransition)):
            merge_task("t1", store, repo, manager, shown_sha=shown)
    verified = verify_task("t1", store, project_subdir="proj", test_argv=["true"], gate_timeout_s=10)
    assert verified.state is TaskState.VERIFIED, verified.failure_reason  # the stale freeze reference was cleared
    audited = audit_task("t1", store, Ledger(tmp_path / "ledger.jsonl"), PassingAudit(), AUDIT_CONFIG,
                         cap_usd=10, project_subdir="proj")
    assert audited.state is TaskState.AUDITED and audited.audited_sha == new_head != old_tip
    assert merge_preview("t1", store, repo).task.audited_sha == new_head
    assert merge_task("t1", store, repo, manager, shown_sha=new_head).state is TaskState.MERGED


def test_a_conflict_refusal_quotes_the_conflict_line_not_gits_progress_line(audited):
    store, repo, worktree, task = audited
    conflict_on_main(repo)

    with pytest.raises(RebaseRefused) as raised:
        rebase_task("t1", store, repo)

    message = str(raised.value)
    assert "CONFLICT (" in message and "Rebasing (" not in message
    assert "factory abandon t1" in message and "factory retry t1" in message


def test_a_failure_that_is_not_a_conflict_quotes_gits_reason_and_gives_no_conflict_advice(audited):
    store, repo, worktree, task = audited
    store.save(replace(task, base_sha="0" * 40))

    with pytest.raises(RebaseRefused) as raised:
        rebase_task("t1", store, repo)

    message = str(raised.value)
    assert "git rebase failed" in message and "no reason given" not in message
    assert "abandon" not in message


def test_the_conflict_line_is_found_whatever_locale_the_callers_environment_has(audited, monkeypatch):
    from factory import rebase_task as module
    store, repo, worktree, task = audited
    conflict_on_main(repo)
    monkeypatch.setattr(module, "_NO_USER_CONFIG", {**module._NO_USER_CONFIG, "LC_ALL": "de_DE.UTF-8"})

    with pytest.raises(RebaseRefused, match=r"CONFLICT \(.*abandon"):
        rebase_task("t1", store, repo)
