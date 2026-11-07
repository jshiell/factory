"""THROWAWAY e2e for plan 13 (run in a real terminal: real nono + real uv, needs network for pytest, no API calls, $0).

Scratch repo with a real uv/pytest project. Three gate runs through verify_task with the real gate sandbox:
  1. an agent-planted, gitignored conftest.py that turns every failure into a pass must NOT be loaded
     (the failing test must fail the gate as tests_failed);
  2. a legitimate passing project is verified (reports the time, which includes the venv rebuild);
  3. a .pth planted inside the ignored .venv must not run (PWNED never appears).
Never touches this repo.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

from dataclasses import replace  # noqa: E402

from factory.settings import Settings  # noqa: E402
from factory.stages import gate_sandbox_for  # noqa: E402
from factory.store import TaskStore  # noqa: E402
from factory.task import Task, TaskState  # noqa: E402
from factory.verify_task import verify_task  # noqa: E402
from factory.worktrees import WorktreeManager  # noqa: E402

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
results = []


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout.strip()


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"(got {got!r}, want {want!r})")


def main():
    real_before = (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list"))
    scratch = Path(tempfile.mkdtemp(prefix="clean-gate-e2e-", dir=Path.home())).resolve()
    repo, state, wt_root = scratch / "repo", scratch / "state", scratch / "wt"
    (repo / "proj").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "proj" / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0"\nrequires-python = ">=3.10"\ndependencies = []\n\n'
        '[dependency-groups]\ndev = ["pytest"]\n')
    (repo / "proj" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (state / "tasks").mkdir(parents=True)
    store, manager = TaskStore(state / "tasks"), WorktreeManager(repo, wt_root)
    settings = replace(Settings(repo, state, wt_root), test_argv=("uv", "run", "pytest", "-q"), project_subdir="proj", gate_timeout_s=120)
    sandbox = gate_sandbox_for(settings)

    def gate(task_id, writer):
        path, base = manager.create(task_id)
        writer(path / "proj")
        store.save(Task(id=task_id, prompt="p", state=TaskState.AGENT_DONE, goal="g", worktree=str(path), base_sha=base))
        started = time.monotonic()
        result = verify_task(task_id, store, "proj", list(settings.test_argv), settings.gate_timeout_s, sandbox,
                             factory_paths=(state, wt_root))
        return result, path, time.monotonic() - started

    def failing_with_planted_conftest(proj):
        (proj / "test_add.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 3\n")
        (proj / ".gitignore").write_text("conftest.py\n")
        (proj / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
            "    outcome = yield\n    outcome.get_result().outcome = 'passed'\n")

    result, path, took = gate("c1", failing_with_planted_conftest)
    check("planted ignored conftest is not loaded: the failing test fails the gate", result.failure_reason, "tests_failed")
    check("the planted conftest is gone from the worktree", (path / "proj" / "conftest.py").exists(), False)

    def passing(proj):
        (proj / "test_add.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n")

    result, path, took = gate("c2", passing)
    check("a legitimate project is verified", result.state, TaskState.VERIFIED)
    print(f"INFO  gate run with venv rebuild took {took:.1f}s (timeout {settings.gate_timeout_s}s)")

    # a .pth planted in the ignored .venv after a real run: the next gate must delete it before pytest starts
    path, base = manager.create("c3")
    (path / "proj" / "test_add.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n")
    store.save(Task(id="c3", prompt="p", state=TaskState.AGENT_DONE, goal="g", worktree=str(path), base_sha=base))
    verify_task("c3", store, "proj", list(settings.test_argv), settings.gate_timeout_s, sandbox, factory_paths=(state, wt_root))
    # c3 is now verified; reuse the worktree as a fresh agent_done task carrying a planted .pth
    site = next((path / "proj" / ".venv").glob("lib/python*/site-packages"), None)
    print("INFO  .venv left after verified run:", site is not None, "(the gate cleans before running, not after)")
    if site is not None:
        (site / "zz.pth").write_text("import os; open('PWNED', 'w').write('x')\n")
    store.save(Task(id="c3", prompt="p", state=TaskState.AGENT_DONE, goal="g", worktree=str(path), base_sha=base))
    result = verify_task("c3", store, "proj", list(settings.test_argv), settings.gate_timeout_s, sandbox, factory_paths=(state, wt_root))
    check("the planted .pth did not run", (path / "proj" / "PWNED").exists(), False)
    check("second gate run still verifies", result.state, TaskState.VERIFIED)

    check("this repo's worktrees and branches are untouched", (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list")), real_before)
    print("scratch dir:", scratch)
    sys.exit(0 if all(results) else 1)


main()
