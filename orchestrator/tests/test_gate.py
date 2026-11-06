import stat

from gitutil import git

from factory.gate import commit_all


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
