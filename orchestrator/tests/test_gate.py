import stat

import pytest

from gitutil import git

from factory.gate import ProjectSubdirEscapes, check_gate, commit_all


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


def test_gate_fails_with_no_changes_when_only_files_outside_the_project_changed(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "scratch.txt").write_text("junk")
    git(repo, "add", "scratch.txt")
    git(repo, "commit", "-q", "-m", "junk only")

    result = gate(repo, base)

    assert result.reason == "no_changes"


def repo_with_project_change(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "proj" / "code.py").write_text("y")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "change")
    return base


def test_gate_fails_with_tests_failed_keeping_the_exit_code(repo):
    base = repo_with_project_change(repo)

    result = gate(repo, base, test_argv=["sh", "-c", "exit 3"])

    assert result.passed is False
    assert result.reason == "tests_failed"
    assert result.test_exit_code == 3


def test_tests_run_in_the_project_subdir_of_the_worktree(repo):
    base = repo_with_project_change(repo)

    result = gate(repo, base, test_argv=["sh", "-c", 'test "$(basename "$PWD")" = proj'])

    assert result.passed is True


def test_hanging_tests_fail_the_gate_with_tests_timeout(repo):
    base = repo_with_project_change(repo)

    result = check_gate(repo, base, "proj", ["sleep", "5"], timeout_s=0.3)

    assert result.passed is False
    assert result.reason == "tests_timeout"


@pytest.mark.parametrize("subdir", ["..", "../elsewhere", "/etc", "proj/../.."])
def test_project_subdir_escaping_the_worktree_is_rejected(repo, subdir):
    base = repo_with_project_change(repo)

    with pytest.raises(ProjectSubdirEscapes):
        gate(repo, base, project_subdir=subdir)


def repo_with_test_file(repo, content="a\nb\n", name="test_calc.py"):
    (repo / "proj").mkdir(exist_ok=True)
    (repo / "proj" / name).write_text(content)
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "tests")
    return git(repo, "rev-parse", "HEAD")


def commit_project_edit(repo, name, content):
    (repo / "proj" / name).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "edit")


def test_adding_lines_to_an_existing_test_file_passes(repo):
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "test_calc.py", "a\nb\nc\n")

    assert gate(repo, base).passed is True


def test_removing_lines_from_an_existing_test_file_fails_tests_modified(repo):
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "test_calc.py", "a\n")

    result = gate(repo, base)

    assert result.passed is False
    assert result.reason == "tests_modified"


def test_deleting_an_existing_empty_test_file_fails_tests_modified(repo):
    base = repo_with_test_file(repo, content="")
    (repo / "proj" / "test_calc.py").unlink()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "delete")

    assert gate(repo, base).reason == "tests_modified"


def test_renaming_an_existing_test_file_fails_tests_modified(repo):
    base = repo_with_test_file(repo)
    git(repo, "mv", "proj/test_calc.py", "proj/test_other.py")
    git(repo, "commit", "-q", "-m", "rename")

    assert gate(repo, base).reason == "tests_modified"


def test_turning_an_existing_test_file_into_a_symlink_fails_tests_modified(repo):
    base = repo_with_test_file(repo)
    (repo / "proj" / "test_calc.py").unlink()
    (repo / "proj" / "test_calc.py").symlink_to("code.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "symlink")

    assert gate(repo, base).reason == "tests_modified"


def test_a_new_test_file_and_a_changed_non_test_file_pass(repo):
    base = repo_with_test_file(repo)
    (repo / "proj" / "test_new.py").write_text("t\n")
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "more")

    assert gate(repo, base).passed is True


PROTECTED = ["conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "sub/conftest.py"]


@pytest.mark.parametrize("name", PROTECTED)
def test_adding_a_protected_file_fails_tests_modified(repo, name):
    base = repo_with_test_file(repo)
    (repo / "proj" / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / "proj" / name).write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "protected")

    assert gate(repo, base).reason == "tests_modified"


@pytest.mark.parametrize("name", PROTECTED)
def test_appending_to_a_protected_file_fails_tests_modified(repo, name):
    (repo / "proj" / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / "proj" / name).write_text("x\n")
    base = repo_with_test_file(repo)
    commit_project_edit(repo, name, "x\ny\n")

    assert gate(repo, base).reason == "tests_modified"


PYPROJECT = '[project]\nname = "p"\ndependencies = []\n\n[tool.pytest.ini_options]\naddopts = "-q"\n'


def test_changing_the_pytest_table_of_pyproject_fails_tests_modified(repo):
    base = repo_with_test_file(repo)
    (repo / "proj" / "pyproject.toml").write_text(PYPROJECT)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add table")
    mid = git(repo, "rev-parse", "HEAD")
    commit_project_edit(repo, "pyproject.toml", PYPROJECT.replace('"-q"', '"-q --deselect x"'))

    assert gate(repo, base).reason == "tests_modified"
    assert gate(repo, mid).reason == "tests_modified"


def test_adding_a_dependency_to_pyproject_passes(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "pyproject.toml").write_text(PYPROJECT)
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "pyproject.toml", PYPROJECT.replace("dependencies = []", 'dependencies = ["requests"]'))

    assert gate(repo, base).passed is True


def test_tamper_is_reported_before_tests_run(repo, tmp_path):
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "test_calc.py", "a\n")
    sentinel = tmp_path / "ran"

    result = gate(repo, base, test_argv=["sh", "-c", f"touch {sentinel}; exit 1"])

    assert result.reason == "tests_modified"
    assert not sentinel.exists()


def test_test_globs_and_protected_names_can_be_overridden(repo):
    base = repo_with_test_file(repo, name="check_calc.py")
    commit_project_edit(repo, "check_calc.py", "a\n")
    custom = dict(test_globs=("check_*.py",), protected_names=("locked.cfg",))

    assert check_gate(repo, base, "proj", ["true"], 10, **custom).reason == "tests_modified"

    base = git(repo, "rev-parse", "HEAD")
    commit_project_edit(repo, "locked.cfg", "x")
    assert check_gate(repo, base, "proj", ["true"], 10, **custom).reason == "tests_modified"
    assert check_gate(repo, base, "proj", ["true"], 10).passed is True


def _alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_gate_timeout_kills_descendants_of_the_test_command(repo, tmp_path):
    import os, signal, time
    base = repo_with_project_change(repo)
    pid_file = tmp_path / "sleeper.pid"

    result = check_gate(repo, base, "proj", ["sh", "-c", f"sleep 31 & echo $! > {pid_file}; wait"], timeout_s=0.5)

    sleeper = int(pid_file.read_text())
    try:
        deadline = time.monotonic() + 3
        while _alive(sleeper) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert result.reason == "tests_timeout" and not _alive(sleeper)
    finally:
        if _alive(sleeper):
            os.kill(sleeper, signal.SIGKILL)


FAKE_WRAPPER = '#!/bin/sh\necho "$@" >> {log}\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n'


def _fake_wrapper(tmp_path):
    wrapper = tmp_path / "fake-nono"
    wrapper.write_text(FAKE_WRAPPER.format(log=tmp_path / "wrapper.log"))
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return wrapper


def test_the_tests_run_through_the_wrapper_prefix(repo, tmp_path):
    base = repo_with_project_change(repo)
    wrapper = _fake_wrapper(tmp_path)

    result = check_gate(repo, base, "proj", ["true"], timeout_s=10, test_prefix=(str(wrapper), "--allow", "x", "--"))

    assert result.passed is True
    assert (tmp_path / "wrapper.log").read_text().split() == ["--allow", "x", "--", "true"]


def test_a_missing_wrapper_fails_the_gate_without_running_the_tests(repo, tmp_path):
    base = repo_with_project_change(repo)
    marker = tmp_path / "ran"

    result = check_gate(repo, base, "proj", ["touch", str(marker)], timeout_s=10,
                        test_prefix=(str(tmp_path / "no-such-nono"), "--"))

    assert (result.passed, result.reason) == (False, "sandbox_unavailable")
    assert not marker.exists()


def test_the_tests_get_exactly_the_given_environment(repo):
    base = repo_with_project_change(repo)

    result = check_gate(repo, base, "proj", ["sh", "-c", 'test "$ONLY" = 1 && test -z "$HOME"'], timeout_s=10,
                        env={"ONLY": "1", "PATH": "/usr/bin:/bin"})

    assert result.passed is True


def test_the_tamper_check_sees_the_cumulative_diff_over_several_commits(repo):
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "test_calc.py", "a\nb\nc\n")
    commit_project_edit(repo, "test_calc.py", "a\n")

    assert gate(repo, base).reason == "tests_modified"


def test_rewriting_a_test_file_the_agent_itself_added_passes(repo):
    (repo / "proj").mkdir()
    base = git(repo, "rev-parse", "HEAD")
    commit_project_edit(repo, "test_new.py", "def test_x():\n    assert False\n")
    commit_project_edit(repo, "test_new.py", "def test_x():\n    assert True\n")

    assert gate(repo, base).passed is True
