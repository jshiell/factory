import stat

from gitutil import git

from factory.gate import check_gate, commit_all


def test_commit_all_commits_new_and_changed_files_with_the_factory_identity(repo):
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("new")

    committed = commit_all(repo, "Task t1")

    assert committed is True
    assert git(repo, "log", "-1", "--format=%s") == "Task t1"
    assert git(repo, "log", "-1", "--format=%an") == "factory"
    assert git(repo, "status", "--porcelain") == ""


def test_commit_all_skips_repository_hooks(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    (repo / "new.txt").write_text("new")

    assert commit_all(repo, "Task t1") is True


def test_commit_all_with_nothing_changed_makes_no_commit(repo):
    before = git(repo, "rev-parse", "HEAD")

    committed = commit_all(repo, "Task t1")

    assert committed is False
    assert git(repo, "rev-parse", "HEAD") == before


PASSING_TESTS = ["true"]


def gate(repo, base_sha, test_argv=PASSING_TESTS, project_subdir="proj"):
    return check_gate(repo, base_sha, project_subdir, test_argv, timeout_s=10)


def test_gate_fails_with_no_commits_when_head_is_the_base(repo):
    base = git(repo, "rev-parse", "HEAD")

    result = gate(repo, base)

    assert result.passed is False
    assert result.reason == "no_commits"


def test_gate_fails_when_head_does_not_descend_from_the_base(repo):
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("s")
    git(repo, "add", "side.txt")
    git(repo, "commit", "-q", "-m", "side")
    side = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    (repo / "main.txt").write_text("m")
    git(repo, "add", "main.txt")
    git(repo, "commit", "-q", "-m", "main")

    result = gate(repo, side)

    assert result.reason == "not_descendant"
