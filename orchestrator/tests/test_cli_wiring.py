import json
import os
import socket
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


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "repo"
    (repo / "target").mkdir(parents=True)
    (repo / "target" / "code.py").write_text("x")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "FAKE_CLAUDE_LOG": str(tmp_path / "argv.log"), "FAKE_NONO_LOG": str(tmp_path / "nono.log")}
    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    nono = tmp_path / "fake-nono"
    nono.write_text(FAKE_NONO)
    nono.chmod(nono.stat().st_mode | stat.S_IEXEC)
    (repo / "factory.toml").write_text(f'claude_executable = "{fake}"\nsandbox_executable = "{nono}"\nstate_dir = "{tmp_path / "state"}"\n'
                                       f'worktree_root = "{tmp_path / "wt"}"\nserver_port = {_free_port()}\n')
    sh("git", "init", "-q", "-b", "main", cwd=repo, env=env)
    sh("git", "add", "-A", cwd=repo, env=env)
    sh("git", "commit", "-q", "-m", "init", cwd=repo, env=env)
    yield repo, env, tmp_path
    if server := _SERVERS.pop(repo, None):
        server.terminate()
        server.wait(timeout=30)


_SERVERS: dict[Path, subprocess.Popen] = {}


def _serving(repo: Path, env) -> None:
    """Start the project's server on first use, so it reads the config the test finished writing."""
    if repo in _SERVERS:
        return
    server = subprocess.Popen(["uv", "run", "--project", str(ORCHESTRATOR), "factory", "serve"], cwd=repo, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    _SERVERS[repo] = server
    announced = server.stdout.readline()
    assert announced.startswith("serving on "), announced + server.stderr.read()


def factory(project, *args, stdin=subprocess.DEVNULL, served=True):
    repo, env, _ = project
    if served and args[0] not in ("--help", "migrate"):
        _serving(repo, env)
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

    result = factory(project, "list", served=False)  # the server could not start on this config either

    assert result.returncode == 2 and "bogus" in result.stderr


def test_merge_from_a_non_terminal_changes_nothing(project):
    task_id = factory(project, "new", "add multiply").stdout.strip()

    result = factory(project, "merge", task_id, stdin=subprocess.PIPE)

    assert result.returncode == 1 and "interactive" in result.stderr
    assert "proposed" in factory(project, "show", task_id).stdout


def test_the_implement_stage_runs_confined_with_no_shell_and_a_read_only_view_of_the_rest(project):
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    repo, _, tmp_path = project
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    SqliteTaskStore(tmp_path / "state" / "factory.db").save(Task(id="t9", prompt="implement it", state=TaskState.QUEUED))

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
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    repo, env, tmp_path = project
    config = repo / "factory.toml"
    config.write_text(config.read_text() + 'test_argv = ["sh", "-c", "echo NEEDS-FIX; test -f fixed"]\n')
    sh("git", "commit", "-q", "-am", "test_argv", cwd=repo, env=env)
    (repo / "target" / "code.py").write_text("changed by the implementer")
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    base = sh("git", "rev-parse", "HEAD", cwd=repo, env=env).stdout.strip()
    SqliteTaskStore(tmp_path / "state" / "factory.db").save(
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


def test_a_task_that_kept_its_plan_is_implemented_with_the_approved_prompt_in_a_fresh_worktree(project):
    from datetime import datetime
    from factory.approve_plan import build_implementation_prompt
    from factory.digest import plan_digest
    from factory.retry_task import retry_with_plan
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    _, _, tmp_path = project
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    store = SqliteTaskStore(tmp_path / "state" / "factory.db")
    plan = "1. add multiply\n2. test it"
    store.save(Task(id="t1", prompt=build_implementation_prompt("add multiply", plan), state=TaskState.FAILED,
                    goal="add multiply", plan=plan, plan_digest=plan_digest(plan), failure_reason="tests_failed"))
    child = retry_with_plan("t1", store, lambda: datetime(2026, 1, 2, 3, 4, 5), plan_digest(plan), ())

    factory(project, "advance", child.id)

    (argv, *_) = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    assert argv[argv.index("-p") + 1] == build_implementation_prompt("add multiply", plan)
    (worktree,) = (tmp_path / "wt").iterdir()
    assert worktree.name == child.id


def test_an_agent_launched_by_the_real_cli_is_recorded_while_it_runs(project):
    from factory.process_registry import load_records
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    repo, _, tmp_path = project
    seen = tmp_path / "seen-records"
    spy = tmp_path / "spy-claude"
    spy.write_text(f'#!/bin/sh\nls "{tmp_path}/state/procs" > "{seen}"\n')
    spy.chmod(spy.stat().st_mode | stat.S_IEXEC)
    config = repo / "factory.toml"
    config.write_text(config.read_text().replace("fake-claude", "spy-claude"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    SqliteTaskStore(tmp_path / "state" / "factory.db").save(Task(id="t9", prompt="implement it", state=TaskState.QUEUED))

    factory(project, "advance", "t9")

    assert seen.read_text().strip().endswith(".json")
    assert load_records(tmp_path / "state") == []


def test_a_gate_run_launched_by_the_real_cli_is_recorded_while_it_runs(project):
    from factory.process_registry import load_records
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    repo, env, tmp_path = project
    seen = tmp_path / "seen-gate-records"
    config = repo / "factory.toml"
    config.write_text(config.read_text() + f'test_argv = ["sh", "-c", "cat {tmp_path}/state/procs/*.json > {seen}"]\n')
    sh("git", "commit", "-q", "-am", "test_argv", cwd=repo, env=env)
    (repo / "target" / "code.py").write_text("changed by the implementer")
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    base = sh("git", "rev-parse", "HEAD", cwd=repo, env=env).stdout.strip()
    SqliteTaskStore(tmp_path / "state" / "factory.db").save(
        Task(id="t9", prompt="stored", state=TaskState.AGENT_DONE, worktree=str(repo), base_sha=base, goal="g", plan="p"))

    factory(project, "advance", "t9")

    assert '"task_id": "t9"' in seen.read_text() and '"stage": "gate"' in seen.read_text()
    assert load_records(tmp_path / "state") == []


FAKE_AUDITOR = r'''#!/usr/bin/env python3
import glob, json, os, sys
argv = sys.argv[1:]
state = os.environ["FAKE_STATE_DIR"]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps({"argv": argv, "cwd": os.getcwd(),
                          "eval_records": sorted(glob.glob(state + "/evals/*/procs/*.json")),
                          "real_records": sorted(glob.glob(state + "/procs/*.json"))}) + "\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                  "structured_output": {"verdict": "pass", "findings": []}}))
'''


def test_eval_audit_runs_the_real_runner_in_the_corpus_project_and_records_under_its_own_directory(project):
    repo, env, tmp_path = project
    auditor = tmp_path / "fake-auditor"
    auditor.write_text(FAKE_AUDITOR)
    auditor.chmod(auditor.stat().st_mode | stat.S_IEXEC)
    config = (repo / "factory.toml").read_text().replace(next(l for l in (repo / "factory.toml").read_text().splitlines()
                                                              if l.startswith("claude_executable")), f'claude_executable = "{auditor}"')
    (repo / "factory.toml").write_text(config + 'project_subdir = "elsewhere"\n')
    env["FAKE_STATE_DIR"] = str(tmp_path / "state")
    project = (repo, env, tmp_path)

    result = factory(project, "eval-audit", "--runs", "1", "--only", "case-01")

    assert result.returncode == 0, result.stdout + result.stderr
    (call,) = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    (eval_dir,) = (tmp_path / "state" / "evals").iterdir()
    assert Path(call["cwd"]).parts[-2:] == ("a1-case-01", "target") or call["cwd"].endswith("/target")
    assert "elsewhere" not in call["cwd"] and "--json-schema" in call["argv"] and "--disallowedTools" in call["argv"]
    assert [Path(p).parent.parent for p in call["eval_records"]] == [eval_dir] and call["real_records"] == []
    nono = [json.loads(line) for line in (tmp_path / "nono.log").read_text().splitlines()]
    assert nono and nono[0][:2] == ["run", "-s"] and "--allow-cwd" in nono[0]
    assert not (tmp_path / "state" / "ledger.jsonl").exists()
    assert list((tmp_path / "state" / "tasks").glob("*.json")) == []
    assert list((eval_dir / "procs").glob("*.json")) == [] and (eval_dir / "case-01-r1.json").is_file()
