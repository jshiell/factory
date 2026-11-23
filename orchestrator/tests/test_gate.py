import stat

import pytest

from factory.gate import ProjectSubdirEscapes, check_gate, commit_all, has_nested_repo
from gitutil import git


@pytest.mark.smoke
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


def test_gate_passes_when_only_files_outside_the_project_changed(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "scratch.txt").write_text("junk")
    git(repo, "add", "scratch.txt")
    git(repo, "commit", "-q", "-m", "junk only")

    result = gate(repo, base)

    assert result.passed is True


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


@pytest.mark.parametrize("subdir", ["..", "../elsewhere", "/etc", "proj/../..", "."])
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


@pytest.mark.parametrize("name", ["uv.toml", ".python-version", ".python-versions"])
def test_adding_a_uv_config_file_beside_a_change_fails_tests_modified(repo, name):
    base = repo_with_test_file(repo)
    commit_project_edit(repo, name, "3.12\n")

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


def test_adding_a_tool_uv_table_to_pyproject_fails_tests_modified(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "pyproject.toml").write_text('[project]\nname = "p"\ndependencies = []\n')
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "pyproject.toml", '[project]\nname = "p"\ndependencies = []\n\n[tool.uv]\nexclude-newer = "2000-01-01T00:00:00Z"\n')

    assert gate(repo, base).reason == "tests_modified"


# Guards (start green): uv config files are protected wherever they sit under the repo.
@pytest.mark.parametrize("name", ["uv.toml", ".python-version", ".python-versions"])
def test_guard_editing_a_uv_config_file_fails_tests_modified(repo, name):
    (repo / "proj").mkdir()
    (repo / "proj" / name).write_text("3.12\n")
    base = repo_with_test_file(repo)
    commit_project_edit(repo, name, "3.13\n")

    assert gate(repo, base).reason == "tests_modified"


@pytest.mark.parametrize("name", ["uv.toml", ".python-version", ".python-versions"])
def test_guard_deleting_a_uv_config_file_fails_tests_modified(repo, name):
    (repo / "proj").mkdir()
    (repo / "proj" / name).write_text("3.12\n")
    base = repo_with_test_file(repo)
    (repo / "proj" / name).unlink()
    commit_project_edit(repo, "code.py", "y")

    assert gate(repo, base).reason == "tests_modified"


def test_guard_a_uv_toml_in_a_project_subdirectory_fails_tests_modified(repo):
    base = repo_with_test_file(repo)
    (repo / "proj" / "sub").mkdir()
    commit_project_edit(repo, "sub/uv.toml", "x\n")

    assert gate(repo, base).reason == "tests_modified"


def test_guard_a_uv_toml_in_a_parent_of_a_nested_project_fails_tests_modified(repo):
    (repo / "a" / "b").mkdir(parents=True)
    (repo / "a" / "b" / "test_x.py").write_text("a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tests")
    base = git(repo, "rev-parse", "HEAD")
    commit_root_file(repo, "a/uv.toml", "x\n")
    (repo / "a" / "b" / "code.py").write_text("y")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "code")

    assert check_gate(repo, base, "a/b", ["true"], 5).reason == "tests_modified"


BASE_WITH_UV = '[project]\nname = "p"\ndependencies = []\n\n[tool.ruff]\nline-length = 80\n\n[tool.uv]\nexclude-newer = "2000-01-01T00:00:00Z"\n'


def pyproject_base(repo, content):
    (repo / "proj").mkdir()
    (repo / "proj" / "pyproject.toml").write_text(content)
    return repo_with_test_file(repo)


# Guards (start green): the [tool.uv] comparison.
def test_guard_editing_the_tool_uv_table_fails_tests_modified(repo):
    base = pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "pyproject.toml", BASE_WITH_UV.replace("2000", "1999"))

    assert gate(repo, base).reason == "tests_modified"


def test_guard_removing_the_tool_uv_table_fails_tests_modified(repo):
    base = pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "pyproject.toml", BASE_WITH_UV.split("[tool.uv]")[0])

    assert gate(repo, base).reason == "tests_modified"


def test_guard_a_root_pyproject_gaining_tool_uv_fails_tests_modified(repo):
    base = repo_with_test_file(repo)
    commit_root_file(repo, "pyproject.toml", '[tool.uv]\nexclude-newer = "2000-01-01T00:00:00Z"\n')
    commit_project_edit(repo, "code.py", "y")

    assert gate(repo, base).reason == "tests_modified"


def test_guard_changes_beside_an_unchanged_tool_uv_table_pass(repo):
    base = pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "pyproject.toml", BASE_WITH_UV.replace("line-length = 80", "line-length = 100"))

    assert gate(repo, base).passed is True


WATCHED_ADDITIONS = {
    "dynamic": ('dependencies = []', 'dependencies = []\ndynamic = ["entry-points"]'),
    "dependency-groups": ("[tool.ruff]", '[dependency-groups]\ndev = ["six"]\n\n[tool.ruff]'),
    "build-system": ("[tool.ruff]", '[build-system]\nrequires = ["setuptools"]\n\n[tool.ruff]'),
    "pytest11": ("[tool.ruff]", '[project.entry-points.pytest11]\nplug = "p.plug"\n\n[tool.ruff]'),
    "scripts": ("[tool.ruff]", '[project.scripts]\nrun = "p:main"\n\n[tool.ruff]'),
    "setuptools": ("[tool.ruff]", '[tool.setuptools]\npackages = ["p"]\n\n[tool.ruff]'),
    "poetry": ("[tool.ruff]", '[tool.poetry.plugins.pytest11]\nplug = "p.plug"\n\n[tool.ruff]'),
    "hatch": ("[tool.ruff]", '[tool.hatch.build.hooks.custom]\npath = "hatch_build.py"\n\n[tool.ruff]'),
}


# Guards (start green): cycle 1's comparison already covers each of these.
@pytest.mark.parametrize("old,new", WATCHED_ADDITIONS.values(), ids=WATCHED_ADDITIONS.keys())
def test_guard_a_watched_pyproject_addition_fails_tests_modified(repo, old, new):
    base = pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "pyproject.toml", BASE_WITH_UV.replace(old, new))

    assert gate(repo, base).reason == "tests_modified"


@pytest.mark.parametrize("old,new", [
    ('name = "p"', 'name = "q"\nversion = "1"\ndescription = "d"'),
    ("line-length = 80", "line-length = 100"),
    ("[tool.ruff]", '[tool.mypy]\nstrict = true\n\n[tool.ruff]'),
], ids=["metadata", "ruff", "mypy"])
def test_guard_an_inert_pyproject_change_passes(repo, old, new):
    base = pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "pyproject.toml", BASE_WITH_UV.replace(old, new))

    assert gate(repo, base).passed is True


def test_changing_a_non_table_project_value_fails_tests_modified(repo):
    base = pyproject_base(repo, 'project = "x"\n')
    commit_project_edit(repo, "pyproject.toml", 'project = "y"\n')

    assert gate(repo, base).reason == "tests_modified"


def test_changing_a_non_table_build_system_value_fails_tests_modified(repo):
    base = pyproject_base(repo, 'build-system = "x"\n')
    commit_project_edit(repo, "pyproject.toml", 'build-system = "y"\n')

    assert gate(repo, base).reason == "tests_modified"


@pytest.mark.parametrize("path", ["sub/pyproject.toml", "sub/deep/PyProject.toml"])
def test_a_nested_pyproject_gaining_an_entry_point_fails_tests_modified(repo, path):
    (repo / "proj" / "sub" / "deep").mkdir(parents=True)
    (repo / "proj" / path).write_text('[project]\nname = "n"\n')
    base = repo_with_test_file(repo)
    commit_project_edit(repo, path, '[project]\nname = "n"\n[project.entry-points.pytest11]\nplug = "n.plug"\n')

    assert gate(repo, base).reason == "tests_modified"


@pytest.mark.parametrize("path", ["setup.py", "hatch_build.py", "sub/Setup.py"])
def test_editing_a_build_script_fails_tests_modified(repo, path):
    (repo / "proj" / "sub").mkdir(parents=True)
    (repo / "proj" / path).write_text("a = 1\n")
    base = repo_with_test_file(repo)
    commit_project_edit(repo, path, "a = 2\n")

    assert gate(repo, base).reason == "tests_modified"


def gate_allowing_dependencies(repo, base_sha):
    return check_gate(repo, base_sha, "proj", PASSING_TESTS, timeout_s=10, allow_dependency_changes=True)


@pytest.mark.parametrize("old,new", [
    ("dependencies = []", 'dependencies = ["requests"]'),
    *WATCHED_ADDITIONS.values(),
], ids=["dependencies", *WATCHED_ADDITIONS.keys()])
def test_allowing_dependency_changes_passes_a_dependency_edit(repo, old, new):
    base = pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "pyproject.toml", BASE_WITH_UV.replace(old, new))

    assert gate_allowing_dependencies(repo, base).passed is True


@pytest.mark.parametrize("path", ["setup.py", "hatch_build.py"])
def test_allowing_dependency_changes_passes_a_build_script_and_lock_edit(repo, path):
    (repo / "proj").mkdir()
    (repo / "proj" / path).write_text("a = 1\n")
    (repo / "proj" / "uv.lock").write_text("a\n")
    base = repo_with_test_file(repo)
    commit_project_edit(repo, path, "a = 2\n")
    commit_project_edit(repo, "uv.lock", "b\n")

    assert gate_allowing_dependencies(repo, base).passed is True


# Guards (start green once the flag exists): the flag never relaxes the tool.uv and tool.pytest watch.
@pytest.mark.parametrize("old,new", [
    ("[tool.ruff]", '[tool.pytest.ini_options]\naddopts = "-p x"\n\n[tool.ruff]'),
    ('exclude-newer = "2000-01-01T00:00:00Z"', 'exclude-newer = "1999-01-01T00:00:00Z"'),
], ids=["pytest", "uv"])
def test_allowing_dependency_changes_still_fails_a_pytest_or_uv_edit(repo, old, new):
    base = pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "pyproject.toml", BASE_WITH_UV.replace(old, new))

    assert gate_allowing_dependencies(repo, base).reason == "tests_modified"


def nested_pyproject_base(repo, content, path="sub/pyproject.toml"):
    (repo / "proj" / path).parent.mkdir(parents=True)
    (repo / "proj" / path).write_text(content)
    return repo_with_test_file(repo)


# Guard (starts green): the flag relaxes a pyproject below the project the same way as the project's own.
@pytest.mark.parametrize("old,new", [
    ("dependencies = []", 'dependencies = ["requests"]'),
    *WATCHED_ADDITIONS.values(),
], ids=["dependencies", *WATCHED_ADDITIONS.keys()])
def test_allowing_dependency_changes_passes_a_nested_pyproject_dependency_edit(repo, old, new):
    base = nested_pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "sub/pyproject.toml", BASE_WITH_UV.replace(old, new))

    assert gate_allowing_dependencies(repo, base).passed is True


NESTED_WATCHED_EDITS = [
    ("[tool.ruff]", '[tool.pytest.ini_options]\naddopts = "-p x"\n\n[tool.ruff]'),
    ('exclude-newer = "2000-01-01T00:00:00Z"', 'exclude-newer = "1999-01-01T00:00:00Z"'),
]


# Guard (starts green): the flag keeps watching tool.pytest and tool.uv in pyprojects below and above the project.
@pytest.mark.parametrize("old,new", NESTED_WATCHED_EDITS, ids=["pytest", "uv"])
def test_allowing_dependency_changes_still_fails_a_nested_pytest_or_uv_edit(repo, old, new):
    base = nested_pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "sub/pyproject.toml", BASE_WITH_UV.replace(old, new))

    assert gate_allowing_dependencies(repo, base).reason == "tests_modified"


def test_allowing_dependency_changes_still_fails_a_root_tool_uv_edit(repo):
    commit_root_file(repo, "pyproject.toml", BASE_WITH_UV)
    base = repo_with_test_file(repo)
    commit_root_file(repo, "pyproject.toml", BASE_WITH_UV.replace("2000", "1999"))
    commit_project_edit(repo, "code.py", "y")

    assert gate_allowing_dependencies(repo, base).reason == "tests_modified"


# Guard (starts green): the flag never relaxes the protected file names.
@pytest.mark.parametrize("name", ["uv.toml", "conftest.py", ".python-version"])
def test_allowing_dependency_changes_still_fails_a_protected_name(repo, name):
    base = repo_with_test_file(repo)
    commit_project_edit(repo, name, "x\n")

    assert gate_allowing_dependencies(repo, base).reason == "tests_modified"


# Guard (starts green): the flag lets a lock be removed; uv re-resolves within the pyproject's ranges.
def test_allowing_dependency_changes_passes_a_deleted_uv_lock(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "uv.lock").write_text("a\n")
    base = repo_with_test_file(repo)
    (repo / "proj" / "uv.lock").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "drop lock")

    assert gate_allowing_dependencies(repo, base).passed is True


def test_adding_a_dependency_fails_tests_modified(repo):
    base = pyproject_base(repo, BASE_WITH_UV)
    commit_project_edit(repo, "pyproject.toml", BASE_WITH_UV.replace("dependencies = []", 'dependencies = ["requests"]'))

    assert gate(repo, base).reason == "tests_modified"


def test_changing_an_existing_uv_lock_fails_tests_modified(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "uv.lock").write_text("a\n")
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "uv.lock", "b\n")

    assert gate(repo, base).reason == "tests_modified"


def test_deleting_an_existing_uv_lock_fails_tests_modified(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "uv.lock").write_text("a\n")
    base = repo_with_test_file(repo)
    (repo / "proj" / "uv.lock").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "drop lock")

    assert gate(repo, base).reason == "tests_modified"


# Guard (starts green): a lock that did not exist in the base only resolves the watched pyproject.
def test_guard_adding_a_uv_lock_passes(repo):
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "uv.lock", "b\n")

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
    custom = {"test_globs": ("check_*.py",), "protected_names": ("locked.cfg",)}

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
    import os
    import signal
    import time
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


def test_appending_a_same_named_test_to_an_existing_test_file_fails_tests_modified(repo):
    base = repo_with_test_file(repo, content="def test_x():\n    assert 1 == 2\n")
    commit_project_edit(repo, "test_calc.py", "def test_x():\n    assert 1 == 2\n\n\ndef test_x():\n    pass\n")

    result = gate(repo, base)

    assert result.passed is False
    assert result.reason == "tests_modified"


def test_appending_a_differently_named_test_to_an_existing_test_file_still_passes(repo):
    base = repo_with_test_file(repo, content="def test_x():\n    pass\n")
    commit_project_edit(repo, "test_calc.py", "def test_x():\n    pass\n\n\ndef test_y():\n    pass\n")

    assert gate(repo, base).passed is True


def test_failing_tests_hand_back_their_output_when_a_capture_limit_is_given(repo):
    base = repo_with_project_change(repo)

    result = check_gate(repo, base, "proj", ["sh", "-c", "echo boom; echo bang >&2; exit 1"], timeout_s=10,
                        capture_limit=500)

    assert result.reason == "tests_failed"
    assert "boom" in result.output and "bang" in result.output


def test_the_output_is_none_without_a_capture_limit_or_for_other_outcomes(repo):
    base = repo_with_project_change(repo)

    uncaptured = gate(repo, base, test_argv=["sh", "-c", "echo boom; exit 1"])
    passed = check_gate(repo, base, "proj", ["true"], timeout_s=10, capture_limit=500)
    timed_out = check_gate(repo, base, "proj", ["sleep", "5"], timeout_s=0.3, capture_limit=500)

    assert (uncaptured.output, passed.output, timed_out.output) == (None, None, None)


def test_the_wrapper_s_own_lines_are_part_of_the_captured_output(repo, tmp_path):
    base = repo_with_project_change(repo)
    wrapper = tmp_path / "noisy-nono"
    wrapper.write_text('#!/bin/sh\necho "nono: applying sandbox" >&2\nshift\nexec "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)

    result = check_gate(repo, base, "proj", ["sh", "-c", "echo boom; exit 1"], timeout_s=10,
                        test_prefix=(str(wrapper), "--"), capture_limit=500)

    assert "nono: applying sandbox" in result.output and "boom" in result.output


MORE_PYTEST_CONFIG = [".pytest.ini", "pytest.toml", ".pytest.toml", "sub/pytest.toml", "sub/.pytest.toml", "sub/.pytest.ini"]


@pytest.mark.parametrize("name", MORE_PYTEST_CONFIG)
def test_adding_any_other_pytest_config_file_fails_tests_modified(repo, name):
    base = repo_with_test_file(repo)
    (repo / "proj" / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / "proj" / name).write_text("[pytest]\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "config")

    assert gate(repo, base).reason == "tests_modified"


NATIVE_TABLE = '[project]\nname = "p"\ndependencies = []\n\n[tool.pytest]\naddopts = ["-q"]\n'


def test_changing_the_native_pytest_table_of_pyproject_fails_tests_modified(repo):
    base = repo_with_test_file(repo)
    (repo / "proj" / "pyproject.toml").write_text(NATIVE_TABLE)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add native table")

    assert gate(repo, base).reason == "tests_modified"


@pytest.mark.parametrize("tool", ["tool = 1\n", "[tool]\npytest = 1\n"])
def test_a_malformed_tool_table_in_pyproject_fails_tests_modified_without_crashing(repo, tool):
    (repo / "proj").mkdir()
    (repo / "proj" / "pyproject.toml").write_text(PYPROJECT)
    base = repo_with_test_file(repo)
    commit_project_edit(repo, "pyproject.toml", tool)

    assert gate(repo, base).reason == "tests_modified"


def frozen_scenario(repo):
    """An implementer commit that appended to an old test and added a new one; returns (base, frozen)."""
    base = repo_with_test_file(repo, content="def test_old():\n    pass\n")
    (repo / "proj" / "code.py").write_text("x")
    commit_project_edit(repo, "test_calc.py", "def test_old():\n    pass\n\n\ndef test_appended():\n    assert False\n")
    commit_project_edit(repo, "test_new.py", "def test_new():\n    assert False\n")
    return base, git(repo, "rev-parse", "HEAD")


def frozen_gate(repo, base, frozen):
    return check_gate(repo, base, "proj", PASSING_TESTS, timeout_s=10, frozen_sha=frozen)


def test_rewriting_an_implementer_added_test_after_the_frozen_commit_fails_tests_modified(repo):
    base, frozen = frozen_scenario(repo)
    commit_project_edit(repo, "test_new.py", "def test_new():\n    assert True\n")

    assert gate(repo, base).passed is True  # the base comparison alone cannot see it
    assert frozen_gate(repo, base, frozen).reason == "tests_modified"


def test_deleting_an_implementer_added_test_file_after_the_frozen_commit_fails_tests_modified(repo):
    base, frozen = frozen_scenario(repo)
    git(repo, "rm", "-q", "proj/test_new.py")
    git(repo, "commit", "-q", "-m", "delete")

    assert gate(repo, base).passed is True
    assert frozen_gate(repo, base, frozen).reason == "tests_modified"


def test_removing_lines_the_implementer_appended_to_an_old_test_file_fails_tests_modified(repo):
    base, frozen = frozen_scenario(repo)
    commit_project_edit(repo, "test_calc.py", "def test_old():\n    pass\n")

    assert gate(repo, base).passed is True
    assert frozen_gate(repo, base, frozen).reason == "tests_modified"


def test_renaming_a_test_file_after_the_frozen_commit_fails_tests_modified(repo):
    base, frozen = frozen_scenario(repo)
    git(repo, "mv", "proj/test_new.py", "proj/test_renamed.py")
    git(repo, "commit", "-q", "-m", "rename")

    assert frozen_gate(repo, base, frozen).reason == "tests_modified"


def test_adding_a_new_test_file_after_the_frozen_commit_passes(repo):
    base, frozen = frozen_scenario(repo)
    commit_project_edit(repo, "test_extra.py", "def test_extra():\n    pass\n")

    assert frozen_gate(repo, base, frozen).passed is True


def test_a_frozen_commit_that_is_not_an_ancestor_fails_not_descendant(repo):
    base, _ = frozen_scenario(repo)
    git(repo, "checkout", "-q", "-b", "side", base)
    (repo / "side.txt").write_text("s")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "side")
    side = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")

    assert frozen_gate(repo, base, side).reason == "not_descendant"


def commit_root_file(repo, name, content):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"root {name}")


def test_a_protected_change_only_outside_the_project_fails_tests_modified(repo):
    base = repo_with_project_change(repo)
    git(repo, "reset", "-q", "--hard", base)
    commit_root_file(repo, "pytest.ini", "[pytest]\naddopts = --collect-only\n")

    assert gate(repo, base).reason == "tests_modified"


def test_a_new_root_pytest_ini_fails_tests_modified(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "pytest.ini", "[pytest]\naddopts = --collect-only\n")

    assert gate(repo, base).reason == "tests_modified"


# Guards: these start green once the pathspec is repo-wide; they pin the intended edges.
@pytest.mark.parametrize("name", ["conftest.py", ".pytest.ini", "pytest.toml", ".pytest.toml", "tox.ini", "setup.cfg", "docs/tox.ini"])
def test_guard_a_protected_file_at_the_root_or_beside_the_project_fails(repo, name):
    base = repo_with_project_change(repo)
    commit_root_file(repo, name, "x\n")

    assert gate(repo, base).reason == "tests_modified"


def test_guard_modifying_and_deleting_a_root_protected_file_fails(repo):
    commit_root_file(repo, "pytest.ini", "[pytest]\n")
    base = repo_with_project_change(repo)
    changed = git(repo, "rev-parse", "HEAD")
    commit_root_file(repo, "pytest.ini", "[pytest]\naddopts = -q\n")
    assert gate(repo, base).reason == "tests_modified"

    git(repo, "reset", "-q", "--hard", changed)
    git(repo, "rm", "-q", "pytest.ini")
    git(repo, "commit", "-q", "-m", "delete")
    assert gate(repo, base).reason == "tests_modified"


def test_guard_renaming_a_file_onto_a_protected_name_fails(repo):
    commit_root_file(repo, "settings.ini", "[pytest]\naddopts = --collect-only\n")
    base = repo_with_project_change(repo)
    git(repo, "mv", "settings.ini", "pytest.ini")
    git(repo, "commit", "-q", "-m", "rename")

    assert gate(repo, base).reason == "tests_modified"


def test_guard_an_unrelated_root_change_beside_a_project_change_passes(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "README.md", "hello\n")

    assert gate(repo, base).passed is True


def test_guard_the_protected_names_override_applies_repo_wide(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "locked.cfg", "x\n")
    custom = {"protected_names": ("locked.cfg",)}
    assert check_gate(repo, base, "proj", ["true"], 5, **custom).reason == "tests_modified"

    git(repo, "reset", "-q", "--hard", "HEAD~1")
    commit_root_file(repo, "pytest.ini", "x\n")
    assert check_gate(repo, base, "proj", ["true"], 5, **custom).passed is True


ROOT_PYPROJECT = '[project]\nname = "r"\n\n[tool.pytest.ini_options]\naddopts = "-q"\n'


def test_a_new_root_pyproject_pytest_table_fails_tests_modified(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "pyproject.toml", ROOT_PYPROJECT)

    assert gate(repo, base).reason == "tests_modified"


def test_guard_a_root_pyproject_change_outside_the_pytest_table_passes(repo):
    commit_root_file(repo, "pyproject.toml", ROOT_PYPROJECT)
    base = repo_with_project_change(repo)
    commit_root_file(repo, "pyproject.toml", ROOT_PYPROJECT.replace('name = "r"', 'name = "renamed"'))

    assert gate(repo, base).passed is True


def test_a_pytest_table_change_in_a_parent_of_a_nested_project_fails_tests_modified(repo):
    (repo / "a" / "b").mkdir(parents=True)
    (repo / "a" / "b" / "code.py").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "nested")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a" / "b" / "code.py").write_text("y")
    (repo / "a" / "pyproject.toml").write_text(ROOT_PYPROJECT)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")

    assert check_gate(repo, base, "a/b", ["true"], 5).reason == "tests_modified"


def test_guard_a_pytest_table_change_in_a_sibling_directory_passes(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "c/pyproject.toml", ROOT_PYPROJECT)

    assert gate(repo, base).passed is True


@pytest.mark.parametrize("subdir", ["./proj", "proj/"])
def test_guard_an_unnormalised_project_subdir_still_watches_its_pyproject(repo, subdir):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "proj/pyproject.toml", ROOT_PYPROJECT)

    assert check_gate(repo, base, subdir, ["true"], 5).reason == "tests_modified"


def test_a_gitlink_added_outside_the_project_fails_outside_project(repo):
    base = repo_with_project_change(repo)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},sub")
    git(repo, "commit", "-q", "-m", "gitlink")

    assert gate(repo, base).reason == "outside_project"


# Plan 87: the whole repo is editable, except the fixed names.
@pytest.mark.parametrize("path", ["proj2/x.py", "proj.txt", "docs/note.md", "sitecustomize.py", ".env"])
def test_guard_any_path_outside_the_project_passes(repo, path):
    base = repo_with_project_change(repo)
    commit_root_file(repo, path, "x\n")

    assert gate(repo, base).passed is True


@pytest.mark.parametrize("name", ["uv.toml", ".python-version", ".python-versions"])
def test_a_root_uv_config_file_fails_tests_modified(repo, name):
    base = repo_with_project_change(repo)
    commit_root_file(repo, name, "x\n")

    assert gate(repo, base).reason == "tests_modified"


@pytest.mark.parametrize("name", ["UV.toml", ".Python-Version", "Conftest.py", "PYTEST.INI"])
def test_a_differently_cased_protected_name_fails_tests_modified(repo, name):
    base = repo_with_test_file(repo)
    commit_project_edit(repo, name, "x\n")

    assert gate(repo, base).reason == "tests_modified"


def test_guard_moving_a_file_out_of_the_project_passes(repo):
    base = repo_with_project_change(repo)
    git(repo, "mv", "proj/code.py", "moved.py")
    commit_project_edit(repo, "other.py", "z")

    assert gate(repo, base).passed is True


def test_guard_deleting_a_file_outside_the_project_passes(repo):
    commit_root_file(repo, "notes.txt", "x\n")
    base = repo_with_project_change(repo)
    git(repo, "rm", "-q", "notes.txt")
    git(repo, "commit", "-q", "-m", "delete")

    assert gate(repo, base).passed is True


@pytest.mark.parametrize("subdir", ["./proj", "proj/"])
def test_guard_an_unnormalised_subdir_allows_project_and_outside_changes(repo, subdir):
    base = repo_with_project_change(repo)
    assert check_gate(repo, base, subdir, ["true"], 5).passed is True

    commit_root_file(repo, "README.md", "x\n")
    assert check_gate(repo, base, subdir, ["true"], 5).passed is True


def test_guard_a_nested_project_allows_a_change_beside_it(repo):
    (repo / "a" / "b").mkdir(parents=True)
    (repo / "a" / "b" / "code.py").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "nested")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a" / "b" / "code.py").write_text("y")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "inside")
    assert check_gate(repo, base, "a/b", ["true"], 5).passed is True

    (repo / "a" / "other.py").write_text("z")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "beside")
    assert check_gate(repo, base, "a/b", ["true"], 5).passed is True


# gate_allow_outside (plan 50)
def test_an_allowed_root_file_beside_a_project_change_passes(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "README.md", "hello\n")

    result = check_gate(repo, base, "proj", ["true"], 10, allow_outside=("README.md",))

    assert result.passed is True


def test_a_change_only_to_an_allowed_path_counts_as_a_change(repo):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    base = git(repo, "rev-parse", "HEAD")
    commit_root_file(repo, "README.md", "hello\n")

    allowed = check_gate(repo, base, "proj", ["true"], 10, allow_outside=("README.md",))
    unlisted = check_gate(repo, base, "proj", ["true"], 10)

    assert (allowed.passed, unlisted.passed) == (True, True)


def test_a_new_symlink_under_an_allowed_directory_fails_outside_project(repo):
    base = repo_with_project_change(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "link").symlink_to("../proj/code.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "symlink")

    result = check_gate(repo, base, "proj", ["true"], 10, allow_outside=("docs",))

    assert result.reason == "outside_project"


def test_guard_a_file_turned_into_a_symlink_under_an_allowed_directory_fails(repo):
    commit_root_file(repo, "docs/a.md", "a\n")
    base = repo_with_project_change(repo)
    (repo / "docs" / "a.md").unlink()
    (repo / "docs" / "a.md").symlink_to("../proj/code.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "swap")

    result = check_gate(repo, base, "proj", ["true"], 10, allow_outside=("docs",))

    assert result.reason == "outside_project"


def test_a_gitlink_under_an_allowed_directory_fails_outside_project(repo):
    base = repo_with_project_change(repo)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},docs/sub")
    git(repo, "commit", "-q", "-m", "gitlink")

    result = check_gate(repo, base, "proj", ["true"], 10, allow_outside=("docs",))

    assert result.reason == "outside_project"


def test_guard_an_unchanged_symlink_under_an_allowed_directory_passes(repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "link").symlink_to("../proj/code.py")
    commit_root_file(repo, "docs/a.md", "a\n")
    base = repo_with_project_change(repo)
    commit_root_file(repo, "docs/a.md", "changed\n")

    result = check_gate(repo, base, "proj", ["true"], 10, allow_outside=("docs",))

    assert result.passed is True


@pytest.mark.parametrize("name", ["docs/conftest.py", "docs/UV.toml"])
def test_guard_a_protected_file_alone_under_an_allowed_directory_fails_tests_modified(repo, name):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    base = git(repo, "rev-parse", "HEAD")
    commit_root_file(repo, name, "x\n")

    result = check_gate(repo, base, "proj", ["true"], 10, allow_outside=("docs",))

    assert result.reason == "tests_modified"


def test_guard_a_listed_directory_covers_nested_files_and_a_prefix_sibling_passes_too(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "docs/guide/a.md", "a\n")
    assert check_gate(repo, base, "proj", ["true"], 10, allow_outside=("docs",)).passed is True

    commit_root_file(repo, "docs2/x.md", "x\n")
    assert check_gate(repo, base, "proj", ["true"], 10, allow_outside=("docs",)).passed is True


def test_guard_an_unlisted_root_file_beside_a_listed_one_passes(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "README.md", "hello\n")
    commit_root_file(repo, "LICENSE", "mit\n")

    result = check_gate(repo, base, "proj", ["true"], 10, allow_outside=("README.md",))

    assert result.passed is True


# Plan 87: the editable area is the repo except `.factory/` and a few fixed names.
@pytest.mark.parametrize("path", [".factory/factory.toml", ".FACTORY/notes.txt", ".Factory/deep/x"])
def test_a_change_under_the_factory_dir_fails_outside_project_even_alone(repo, path):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    base = git(repo, "rev-parse", "HEAD")
    commit_root_file(repo, path, "x\n")

    assert gate(repo, base).reason == "outside_project"


def test_a_change_under_the_factory_dir_fails_even_when_allowed_outside(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, ".factory/factory.toml", "x\n")

    result = check_gate(repo, base, "proj", ["true"], 10, allow_outside=(".factory",))

    assert result.reason == "outside_project"


@pytest.mark.parametrize("name", ["factory.toml", "Factory.TOML"])
def test_a_root_factory_toml_fails_outside_project_even_alone(repo, name):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    base = git(repo, "rev-parse", "HEAD")
    commit_root_file(repo, name, "x\n")

    assert gate(repo, base).reason == "outside_project"


def test_guard_a_factory_toml_below_the_root_is_an_ordinary_file(repo):
    base = repo_with_project_change(repo)
    commit_root_file(repo, "proj/factory.toml", "x\n")

    assert gate(repo, base).passed is True


@pytest.mark.parametrize("path", [
    ".gitattributes", ".gitmodules", "proj/.gitattributes", "proj/sub/.GitModules", "docs/.GITATTRIBUTES",
])
def test_a_gitattributes_or_gitmodules_at_any_depth_fails_outside_project(repo, path):
    base = repo_with_project_change(repo)
    commit_root_file(repo, path, "x\n")

    assert check_gate(repo, base, "proj", ["true"], 10, allow_outside=(".gitattributes", ".gitmodules", "docs")).reason == "outside_project"


def test_removing_a_test_file_outside_the_project_fails_tests_modified(repo):
    commit_root_file(repo, "other/test_other.py", "def test_o():\n    pass\n")
    base = repo_with_project_change(repo)
    git(repo, "rm", "-q", "other/test_other.py")
    git(repo, "commit", "-q", "-m", "delete")

    assert gate(repo, base).reason == "tests_modified"


def test_rewriting_an_implementer_added_test_outside_the_project_after_the_frozen_commit_fails_tests_modified(repo):
    base, _ = frozen_scenario(repo)
    commit_root_file(repo, "other/test_added.py", "def test_a():\n    assert False\n")
    frozen = git(repo, "rev-parse", "HEAD")
    commit_root_file(repo, "other/test_added.py", "def test_a():\n    assert True\n")

    assert frozen_gate(repo, base, frozen).reason == "tests_modified"


def add_project_symlink(repo):
    (repo / "proj" / "link").symlink_to("code.py")
    git(repo, "add", "proj/link")


def add_project_gitlink(repo):
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{git(repo, 'rev-parse', 'HEAD')},proj/sub")


@pytest.mark.parametrize("add_link", [add_project_symlink, add_project_gitlink], ids=["symlink", "gitlink"])
def test_a_link_added_inside_the_project_fails_outside_project(repo, add_link):
    base = repo_with_project_change(repo)
    add_link(repo)
    git(repo, "commit", "-q", "-m", "link")

    assert gate(repo, base).reason == "outside_project"


@pytest.mark.parametrize("subdir", [".factory", ".FACTORY/sub", "proj/../.factory/x", "alias"])
def test_a_project_subdir_resolving_into_the_factory_dir_is_rejected(repo, subdir):
    base = repo_with_project_change(repo)
    (repo / ".factory" / "x").mkdir(parents=True)
    (repo / "alias").symlink_to(".factory")

    with pytest.raises(ProjectSubdirEscapes):
        gate(repo, base, project_subdir=subdir)


@pytest.mark.parametrize("form", ["directory", "gitfile"])
def test_a_nested_repo_outside_the_project_is_found(repo, form):
    (repo / "proj").mkdir()
    nested = repo / "docs" / "x"
    nested.mkdir(parents=True)
    if form == "directory":
        (nested / ".git").mkdir()
    else:
        (nested / ".git").write_text("gitdir: /tmp/elsewhere\n")

    assert has_nested_repo(repo, "proj") is True


def test_guard_the_worktree_s_own_git_is_not_a_nested_repo(repo):
    (repo / "proj").mkdir()

    assert has_nested_repo(repo, "proj") is False
