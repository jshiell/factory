import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest

from configutil import config_path, write_config
from test_audit_share import write_eval

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
    home = tmp_path / "factory-home"
    repo = home / "proj" / "repo"
    (repo / "target").mkdir(parents=True)
    (repo / "target" / "code.py").write_text("x")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "FACTORY_HOME": str(home), "FAKE_CLAUDE_LOG": str(tmp_path / "argv.log"), "FAKE_NONO_LOG": str(tmp_path / "nono.log")}
    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    nono = tmp_path / "fake-nono"
    nono.write_text(FAKE_NONO)
    nono.chmod(nono.stat().st_mode | stat.S_IEXEC)
    write_config(repo, f'claude_executable = "{fake}"\nsandbox_executable = "{nono}"\n')
    (home / "factory.toml").write_text(f"server_port = {_free_port()}\n")
    sh("git", "init", "-q", "-b", "main", cwd=repo, env=env)
    sh("git", "add", "-A", cwd=repo, env=env)
    sh("git", "commit", "-q", "-m", "init", cwd=repo, env=env)
    yield repo, env, tmp_path
    if server := _SERVERS.pop(repo, None):
        server.terminate()
        server.wait(timeout=30)


_SERVERS: dict[Path, subprocess.Popen] = {}
_PORTS: dict[Path, int] = {}
JOB_TIMEOUT_SECONDS = 60


def _serving(repo: Path, env) -> None:
    """Start the project's server on first use, so it reads the config the test finished writing."""
    if repo in _SERVERS:
        return
    server = subprocess.Popen(["uv", "run", "--project", str(ORCHESTRATOR), "factory", "serve"], cwd=repo, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    _SERVERS[repo] = server
    announced = server.stdout.readline()
    assert announced.startswith("serving on "), announced + server.stderr.read()
    _PORTS[repo] = int(re.match(r"serving on http://127\.0\.0\.1:(\d+);", announced).group(1))
    _await_listening(_PORTS[repo])


def _await_listening(port: int) -> None:
    """The server announces before it binds, so wait until it accepts connections."""
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            assert time.monotonic() < deadline, f"nothing listening on port {port}"
            time.sleep(0.05)


def factory(project, *args, served=True):
    repo, env, _ = project
    if served and args[0] != "--help":
        _serving(repo, env)
    return sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", *args, cwd=repo, env=env)


def api(project, method, path, **body):
    """One JSON call to the project's real server, scoped to repo `proj`; returns the decoded body, failing on an error status."""
    repo, env, _ = project
    _serving(repo, env)
    token = (Path(env["FACTORY_HOME"]) / "_server" / "token").read_text().strip()
    response = httpx2.request(method, f"http://127.0.0.1:{_PORTS[repo]}/repos/proj{path}", json=body or None,
                              headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert response.status_code < 400, f"{method} {path}: {response.status_code} {response.text}"
    return response.json()


def advance(project, task_id):
    """Queue an advance and wait for its job; returns the finished job, failing if it does not finish in time."""
    job = api(project, "POST", f"/tasks/{task_id}/advance")["job"]
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while job["state"] not in ("succeeded", "failed", "interrupted"):
        assert time.monotonic() < deadline, f"advance job {job['id']} did not finish: {job}"
        time.sleep(0.1)
        job = api(project, "GET", f"/jobs/{job['id']}")
    return job


@pytest.mark.smoke
def test_help_exits_0_and_names_the_commands(project):
    result = factory(project, "--help")

    assert result.returncode == 0
    for command in ("serve", "ui", "eval-audit", "eval-export", "init", "clone"):
        assert command in result.stdout


def test_init_writes_the_global_template_without_needing_a_repo_or_a_server(project):
    _, env, tmp_path = project
    (tmp_path / "factory-home" / "factory.toml").unlink()  # the fixture's server_port file

    result = sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", "init", cwd=tmp_path, env=env,
                stdin=subprocess.DEVNULL)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "factory-home" / "factory.toml").read_text().startswith("# Global factory settings")


def test_clone_without_a_repository_argument_is_a_usage_error_needing_no_repo_or_server(project):
    _, env, tmp_path = project

    result = sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", "clone", cwd=tmp_path, env=env,
                stdin=subprocess.DEVNULL)

    assert result.returncode == 2
    assert "usage error" in result.stderr


def test_clone_refuses_a_non_github_repository_and_an_init_with_extra_arguments_is_a_usage_error(project):
    _, env, tmp_path = project
    (tmp_path / "factory-home" / "factory.toml").unlink()  # the fixture's server_port file

    def run(*args):
        return sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", *args, cwd=tmp_path, env=env,
                  stdin=subprocess.DEVNULL)

    refused = run("clone", "https://gitlab.com/o/r")
    extra = run("init", "extra")

    assert refused.returncode == 1 and "not a GitHub repository" in refused.stderr
    assert extra.returncode == 2 and "usage error" in extra.stderr
    assert not (tmp_path / "factory-home" / "factory.toml").exists()


def test_clone_refuses_an_existing_target_through_the_cli(project):
    _, env, tmp_path = project
    target = tmp_path / "factory-home" / "r" / "repo"
    target.mkdir(parents=True)

    result = sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", "clone", "o/r", cwd=tmp_path, env=env,
                stdin=subprocess.DEVNULL)

    assert result.returncode == 1
    assert f"{target} already exists; not overwriting" in result.stderr


def test_the_repo_option_names_the_repo_when_the_command_runs_elsewhere(project):
    repo, env, tmp_path = project
    _serving(repo, env)

    named = sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", "--repo", "proj", "eval-audit", "--check-corpus",
               cwd=tmp_path, env=env, stdin=subprocess.DEVNULL)
    unnamed = sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", "eval-audit", "--check-corpus", cwd=tmp_path,
                 env=env, stdin=subprocess.DEVNULL)

    assert "no repo" not in named.stderr, named.stderr
    assert unnamed.returncode == 2 and "no repo" in unnamed.stderr


@pytest.mark.smoke
def test_advance_plans_and_reviews_with_read_only_tools_and_stops_at_reviewed(project):
    _, _, tmp_path = project
    task_id = api(project, "POST", "/tasks", goal="add multiply")["id"]

    job = advance(project, task_id)

    assert job["state"] == "succeeded", job
    assert job["result"]["steps"] == [{"stage": "plan", "state": "planned"}, {"stage": "review", "state": "reviewed"}]
    assert job["result"]["state"] == "reviewed"
    calls = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    assert len(calls) == 2
    (worktree,) = (tmp_path / "factory-home" / "proj" / "worktrees").iterdir()
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
    shown = api(project, "GET", f"/tasks/{task_id}")
    assert shown["state"] == "reviewed" and "1. add multiply" in shown["plan"]


def test_a_bad_config_file_exits_2_naming_the_key(project):
    repo, _, _ = project
    write_config(repo, "bogus = 1\n")

    result = factory(project, "eval-audit", "--check-corpus", served=False)  # the server could not start on this config either

    assert result.returncode == 2 and "bogus" in result.stderr


def test_the_implement_stage_runs_confined_with_no_shell_in_its_worktree(project):
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    repo, _, tmp_path = project
    (tmp_path / "factory-home" / "proj" / "state").mkdir(parents=True, exist_ok=True)
    SqliteTaskStore((tmp_path / "factory-home" / "proj" / "state") / "factory.db").save(Task(id="t9", prompt="implement it", state=TaskState.QUEUED))

    advance(project, "t9")

    (argv,) = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    (worktree,) = (tmp_path / "factory-home" / "proj" / "worktrees").iterdir()
    assert "--allowedTools" not in argv
    disallowed = argv[argv.index("--disallowedTools") + 1]
    assert disallowed == "Bash Bash(git *) Glob NotebookEdit"
    assert argv[argv.index("--add-dir") + 1] == str(worktree.resolve())
    (sandbox_argv,) = [json.loads(line) for line in (tmp_path / "nono.log").read_text().splitlines()]
    assert sandbox_argv[:7] == ["run", "-s", "--profile", "nolabs-ai/claude", "--allow-cwd", "--read", str(worktree.resolve())]


def test_the_agent_is_not_granted_a_directory_that_holds_the_server_state(project):
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    _, _, tmp_path = project  # the fake claude sits in tmp_path, which contains the state directory
    (tmp_path / "factory-home" / "proj" / "state").mkdir(parents=True, exist_ok=True)
    SqliteTaskStore((tmp_path / "factory-home" / "proj" / "state") / "factory.db").save(Task(id="t8", prompt="implement it", state=TaskState.QUEUED))

    advance(project, "t8")

    (worktree,) = (tmp_path / "factory-home" / "proj" / "worktrees").iterdir()
    (sandbox_argv,) = [json.loads(line) for line in (tmp_path / "nono.log").read_text().splitlines()]
    reads = [sandbox_argv[i + 1] for i, arg in enumerate(sandbox_argv) if arg == "--read"]
    assert reads == [str(worktree.resolve())]


def test_a_failing_gate_runs_one_confined_fix_round_and_the_tests_then_pass(project):
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    from factory.worktrees import WorktreeManager
    repo, env, tmp_path = project
    config = config_path(repo)
    config.write_text(config.read_text() + 'test_argv = ["sh", "-c", "echo NEEDS-FIX; test -f fixed"]\n')
    sh("git", "commit", "-q", "-am", "test_argv", cwd=repo, env=env)
    worktree, base = WorktreeManager(repo, (tmp_path / "factory-home" / "proj" / "worktrees")).create("t9")
    (worktree / "target" / "code.py").write_text("changed by the implementer")
    (tmp_path / "factory-home" / "proj" / "state").mkdir(parents=True, exist_ok=True)
    SqliteTaskStore((tmp_path / "factory-home" / "proj" / "state") / "factory.db").save(
        Task(id="t9", prompt="stored", state=TaskState.AGENT_DONE, worktree=str(worktree), base_sha=base, goal="g", plan="p"))

    job = advance(project, "t9")

    assert job["state"] == "succeeded" and ("verify", "verified") in [(s["stage"], s["state"]) for s in job["result"]["steps"]], job  # the fake audit is out of scope
    calls = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    (fix,) = [argv for argv in calls if "--json-schema" not in argv]
    assert "NEEDS-FIX" in fix[fix.index("-p") + 1]
    assert re.search(r"<test-output-[0-9a-f]+>", fix[fix.index("-p") + 1])
    assert fix[fix.index("--disallowedTools") + 1] == "Bash Bash(git *) Glob NotebookEdit"
    nono_calls = [json.loads(line) for line in (tmp_path / "nono.log").read_text().splitlines()]
    assert any(argv[argv.index("--") + 1].endswith("fake-claude") and "--profile" in argv for argv in nono_calls)
    shown = api(project, "GET", "/tasks/t9")
    assert shown["state"] == "verified" and shown["fix_rounds_used"] == 1 and "NEEDS-FIX" not in json.dumps(shown)


def test_a_task_that_kept_its_plan_is_implemented_with_the_approved_prompt_in_a_fresh_worktree(project):
    from datetime import datetime

    from factory.approve_plan import build_implementation_prompt
    from factory.digest import plan_digest
    from factory.retry_task import retry_with_plan
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    _, _, tmp_path = project
    (tmp_path / "factory-home" / "proj" / "state").mkdir(parents=True, exist_ok=True)
    store = SqliteTaskStore((tmp_path / "factory-home" / "proj" / "state") / "factory.db")
    plan = "1. add multiply\n2. test it"
    store.save(Task(id="t1", prompt=build_implementation_prompt("add multiply", plan), state=TaskState.FAILED,
                    goal="add multiply", plan=plan, plan_digest=plan_digest(plan), failure_reason="tests_failed"))
    child = retry_with_plan("t1", store, lambda: datetime(2026, 1, 2, 3, 4, 5), plan_digest(plan), ())

    advance(project, child.id)

    (argv, *_) = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    assert argv[argv.index("-p") + 1] == build_implementation_prompt("add multiply", plan)
    (worktree,) = (tmp_path / "factory-home" / "proj" / "worktrees").iterdir()
    assert worktree.name == child.id


QUERY_RECORDS = """#!{python}
import sqlite3, sys
rows = sqlite3.connect(sys.argv[1]).execute("select task_id, stage from processes order by pid").fetchall()
open(sys.argv[2], "w").write("".join(f"{{task}},{{stage}}\\n" for task, stage in rows))
"""


def query_records_script(tmp_path):
    """A script that writes `task,stage` for every row of the process table: `script DB OUT`."""
    script = tmp_path / "query-records"
    script.write_text(QUERY_RECORDS.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_an_agent_launched_by_the_real_server_is_recorded_while_it_runs(project):
    from factory.sqlite_process_records import SqliteProcessRecords
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    repo, _, tmp_path = project
    seen = tmp_path / "seen-records"
    spy = tmp_path / "spy-claude"
    spy.write_text(f'#!/bin/sh\n"{query_records_script(tmp_path)}" "{tmp_path}/factory-home/proj/state/factory.db" "{seen}"\n')
    spy.chmod(spy.stat().st_mode | stat.S_IEXEC)
    config = config_path(repo)
    config.write_text(config.read_text().replace("fake-claude", "spy-claude"))
    (tmp_path / "factory-home" / "proj" / "state").mkdir(parents=True, exist_ok=True)
    SqliteTaskStore((tmp_path / "factory-home" / "proj" / "state") / "factory.db").save(Task(id="t9", prompt="implement it", state=TaskState.QUEUED))

    advance(project, "t9")

    assert seen.read_text() == "t9,running\n"
    assert SqliteProcessRecords((tmp_path / "factory-home" / "proj" / "state") / "factory.db").load() == []


def test_a_gate_run_launched_by_the_real_server_is_recorded_while_it_runs(project):
    from factory.sqlite_process_records import SqliteProcessRecords
    from factory.sqlite_store import SqliteTaskStore
    from factory.task import Task, TaskState
    from factory.worktrees import WorktreeManager
    repo, env, tmp_path = project
    seen = tmp_path / "seen-gate-records"
    config = config_path(repo)
    config.write_text(config.read_text() + f'test_argv = ["{query_records_script(tmp_path)}", "{tmp_path}/factory-home/proj/state/factory.db", "{seen}"]\n')
    sh("git", "commit", "-q", "-am", "test_argv", cwd=repo, env=env)
    worktree, base = WorktreeManager(repo, (tmp_path / "factory-home" / "proj" / "worktrees")).create("t9")
    (worktree / "target" / "code.py").write_text("changed by the implementer")
    (tmp_path / "factory-home" / "proj" / "state").mkdir(parents=True, exist_ok=True)
    SqliteTaskStore((tmp_path / "factory-home" / "proj" / "state") / "factory.db").save(
        Task(id="t9", prompt="stored", state=TaskState.AGENT_DONE, worktree=str(worktree), base_sha=base, goal="g", plan="p"))

    advance(project, "t9")

    assert seen.read_text() == "t9,gate\n"
    assert SqliteProcessRecords((tmp_path / "factory-home" / "proj" / "state") / "factory.db").load() == []


FAKE_AUDITOR = r'''#!/usr/bin/env python3
import glob, json, os, sqlite3, sys
argv = sys.argv[1:]
state = os.environ["FAKE_STATE_DIR"]
rows = sqlite3.connect(state + "/factory.db").execute("select pid from processes").fetchall()  # fails if there is no table
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps({"argv": argv, "cwd": os.getcwd(),
                          "eval_records": sorted(glob.glob(state + "/evals/*/procs/*.json")),
                          "real_records": rows}) + "\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                  "structured_output": {"verdict": "pass", "findings": []}}))
'''


def test_eval_audit_runs_the_real_runner_in_the_corpus_project_and_records_under_its_own_directory(project):
    repo, env, tmp_path = project
    auditor = tmp_path / "fake-auditor"
    auditor.write_text(FAKE_AUDITOR)
    auditor.chmod(auditor.stat().st_mode | stat.S_IEXEC)
    config = config_path(repo).read_text().replace(next(line for line in config_path(repo).read_text().splitlines()
                                                        if line.startswith("claude_executable")), f'claude_executable = "{auditor}"')
    write_config(repo, config + 'project_subdir = "elsewhere"\n')
    env["FAKE_STATE_DIR"] = str(tmp_path / "factory-home" / "proj" / "state")
    project = (repo, env, tmp_path)

    result = factory(project, "eval-audit", "--runs", "1", "--only", "case-01")

    assert result.returncode == 0, result.stdout + result.stderr
    (call,) = [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]
    (eval_dir,) = ((tmp_path / "factory-home" / "proj" / "state") / "evals").iterdir()
    assert Path(call["cwd"]).parts[-2:] == ("a1-case-01", "target") or call["cwd"].endswith("/target")
    assert "elsewhere" not in call["cwd"] and "--json-schema" in call["argv"] and "--disallowedTools" in call["argv"]
    assert [Path(p).parent.parent for p in call["eval_records"]] == [eval_dir] and call["real_records"] == []
    nono = [json.loads(line) for line in (tmp_path / "nono.log").read_text().splitlines()]
    assert nono and nono[0][:2] == ["run", "-s"] and "--allow-cwd" in nono[0]
    assert not ((tmp_path / "factory-home" / "proj" / "state") / "ledger.jsonl").exists()
    assert list(((tmp_path / "factory-home" / "proj" / "state") / "tasks").glob("*.json")) == []
    assert list((eval_dir / "procs").glob("*.json")) == [] and (eval_dir / "case-01-r1.json").is_file()
    assert not (eval_dir / "factory.db").exists()


def test_eval_export_needs_no_repo_and_no_server(project):
    _, env, tmp_path = project
    directory = write_eval(tmp_path / "evals" / "2026-01-02")

    result = sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", "eval-export", str(directory), cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert (directory / "run.json").is_file() and (directory / "run.md").is_file()


def test_an_unknown_repo_is_refused_without_creating_its_state(project):
    _, env, tmp_path = project
    home = Path(env["FACTORY_HOME"])

    result = sh("uv", "run", "--project", str(ORCHESTRATOR), "factory", "--repo", "typo", "eval-audit", "--check-corpus", cwd=tmp_path, env=env,
                stdin=subprocess.DEVNULL)

    assert result.returncode == 2 and "unknown repo typo" in result.stderr
    assert not (home / "typo").exists()
