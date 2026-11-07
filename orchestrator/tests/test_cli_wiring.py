import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ORCHESTRATOR = Path(__file__).parent.parent

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
if "--json-schema" not in argv:  # the implementer: no structured output
    output = None
    if "<test-output-" in argv[argv.index("-p") + 1]:  # a fix round: do what the failure asked
        open("fixed", "w").write("ok")
else:
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    if "plan" in schema["properties"]:
        output = {"plan": "1. add multiply\n2. add tests", "risks": ["none"]}
    else:
        output = {"verdict": "approve", "findings": ["fine"]}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.01, "structured_output": output}))
'''


FAKE_NONO = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_NONO_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
rest = argv[argv.index("--") + 1:]
os.execvp(rest[0], rest)
'''


def sh(*args, cwd, env=None, stdin=subprocess.DEVNULL):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, stdin=stdin)


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "repo"
    (repo / "target").mkdir(parents=True)
    (repo / "target" / "code.py").write_text("x")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "FAKE_CLAUDE_LOG": str(tmp_path / "argv.log"), "FAKE_NONO_LOG": str(tmp_path / "nono.log")}
    sh("git", "init", "-q", "-b", "main", cwd=repo, env=env)
    sh("git", "add", "-A", cwd=repo, env=env)
    sh("git", "commit", "-q", "-m", "init", cwd=repo, env=env)
    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    nono = tmp_path / "fake-nono"
    nono.write_text(FAKE_NONO)
    nono.chmod(nono.stat().st_mode | stat.S_IEXEC)
    (repo / "factory.toml").write_text(f'claude_executable = "{fake}"\nsandbox_executable = "{nono}"\nstate_dir = "{tmp_path / "state"}"\n'
                                       f'worktree_root = "{tmp_path / "wt"}"\n')
    return repo, env, tmp_path


def factory(project, *args, stdin=subprocess.DEVNULL):
    repo, env, _ = project
    return sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", *args, cwd=repo, env=env, stdin=stdin)


def test_help_exits_0_and_names_the_commands(project):
    result = factory(project, "--help")

    assert result.returncode == 0
    for command in ("new", "advance", "approve", "costs"):
        assert command in result.stdout


def test_new_list_show_work_against_a_fresh_state_directory(project):
    created = factory(project, "new", "add multiply")
    task_id = created.stdout.strip()

    assert created.returncode == 0 and task_id
    assert task_id in factory(project, "list").stdout
    assert "add multiply" in factory(project, "show", task_id).stdout


def test_advance_plans_and_reviews_with_read_only_tools_and_stops_at_reviewed(project):
    _, _, tmp_path = project
    task_id = factory(project, "new", "add multiply").stdout.strip()

    result = factory(project, "advance", task_id)

    assert result.returncode == 0, result.stderr
    assert "plan -> planned" in result.stdout and "review -> reviewed" in result.stdout
    assert f"factory approve {task_id}" in result.stdout
    calls = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    assert len(calls) == 2
    (worktree,) = (tmp_path / "wt").iterdir()
    worktree = worktree.resolve()
    for argv in calls:
        assert "--allowedTools" not in argv
        assert {"Write", "Glob"} <= set(argv[argv.index("--disallowedTools") + 1].split())
        assert "--permission-mode" not in argv
        assert argv[argv.index("--add-dir") + 1] == str(worktree)
    sandbox_calls = [json.loads(line) for line in (tmp_path / "nono.log").read_text().splitlines()]
    assert len(sandbox_calls) == 2
    for argv in sandbox_calls:
        assert argv[:7] == ["run", "-s", "--profile", "nolabs-ai/claude", "--allow-cwd", "--read", str(worktree)]
    shown = factory(project, "show", task_id).stdout
    assert "reviewed" in shown and "1. add multiply" in shown


def test_approve_from_a_non_terminal_changes_nothing(project):
    task_id = factory(project, "new", "add multiply").stdout.strip()
    factory(project, "advance", task_id)

    result = factory(project, "approve", task_id, stdin=subprocess.PIPE)

    assert result.returncode == 1 and "interactive" in result.stderr
    assert "reviewed" in factory(project, "show", task_id).stdout


def test_a_bad_config_file_exits_2_naming_the_key(project):
    repo, _, _ = project
    (repo / "factory.toml").write_text("bogus = 1\n")

    result = factory(project, "list")

    assert result.returncode == 2 and "bogus" in result.stderr


def test_merge_from_a_non_terminal_changes_nothing(project):
    task_id = factory(project, "new", "add multiply").stdout.strip()

    result = factory(project, "merge", task_id, stdin=subprocess.PIPE)

    assert result.returncode == 1 and "interactive" in result.stderr
    assert "proposed" in factory(project, "show", task_id).stdout


def test_the_implement_stage_runs_confined_with_no_shell_and_a_read_only_view_of_the_rest(project):
    from factory.store import TaskStore
    from factory.task import Task, TaskState
    repo, _, tmp_path = project
    (tmp_path / "state" / "tasks").mkdir(parents=True)
    TaskStore(tmp_path / "state" / "tasks").save(Task(id="t9", prompt="implement it", state=TaskState.QUEUED))

    factory(project, "advance", "t9")

    (argv,) = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    (worktree,) = (tmp_path / "wt").iterdir()
    assert "--allowedTools" not in argv
    disallowed = argv[argv.index("--disallowedTools") + 1]
    assert disallowed == "Bash Bash(git *) Glob NotebookEdit"
    assert argv[argv.index("--add-dir") + 1] == str(worktree.resolve())
    (sandbox_argv,) = [json.loads(line) for line in (tmp_path / "nono.log").read_text().splitlines()]
    assert sandbox_argv[:7] == ["run", "-s", "--profile", "nolabs-ai/claude", "--allow-cwd", "--read", str(worktree.resolve())]


def test_a_failing_gate_runs_one_confined_fix_round_and_the_tests_then_pass(project):
    import re
    from factory.store import TaskStore
    from factory.task import Task, TaskState
    repo, env, tmp_path = project
    config = repo / "factory.toml"
    config.write_text(config.read_text() + 'test_argv = ["sh", "-c", "echo NEEDS-FIX; test -f fixed"]\n')
    (repo / "target" / "code.py").write_text("changed by the implementer")
    (tmp_path / "state" / "tasks").mkdir(parents=True)
    base = sh("git", "rev-parse", "HEAD", cwd=repo, env=env).stdout.strip()
    TaskStore(tmp_path / "state" / "tasks").save(
        Task(id="t9", prompt="stored", state=TaskState.AGENT_DONE, worktree=str(repo), base_sha=base, goal="g", plan="p"))

    result = factory(project, "advance", "t9")

    assert "verify -> verified" in result.stdout, result.stdout + result.stderr  # the fake audit is out of scope
    calls = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    (fix,) = [argv for argv in calls if "--json-schema" not in argv]
    assert "NEEDS-FIX" in fix[fix.index("-p") + 1]
    assert re.search(r"<test-output-[0-9a-f]+>", fix[fix.index("-p") + 1])
    assert fix[fix.index("--disallowedTools") + 1] == "Bash Bash(git *) Glob NotebookEdit"
    nono_calls = [json.loads(line) for line in (tmp_path / "nono.log").read_text().splitlines()]
    assert any(argv[argv.index("--") + 1].endswith("fake-claude") and "--profile" in argv for argv in nono_calls)
    shown = factory(project, "show", "t9").stdout
    assert "verified" in shown and "NEEDS-FIX" not in shown and "fix_rounds_used: 1" in shown
