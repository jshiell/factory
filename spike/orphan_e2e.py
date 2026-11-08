"""THROWAWAY e2e for plan 21 (needs real `ps`, `nono` on PATH, uv). Run from anywhere: python3 spike/orphan_e2e.py

Starts a real `factory advance`, SIGKILLs the driver while (a) a nono-wrapped fake slow agent and (b) a slow gate
test run are alive, checks both trees survive, then runs `factory recover` / `factory abandon` and checks every
process of the tree is gone. Makes no API calls."""
import json, os, signal, stat, subprocess, sys, tempfile, time
from pathlib import Path

ORCH = Path(__file__).resolve().parent.parent / "orchestrator"
SLEEP_AGENT, SLEEP_GATE = "31337", "31338"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def sh(*args, cwd, env, **kw):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw)


def sleepers(marker):
    return subprocess.run(["pgrep", "-f", f"sleep {marker}"], capture_output=True, text=True).stdout.split()


def wait_for(fn, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.3)
    return False


def factory(repo, env, *args, **kw):
    return sh("uv", "run", "--project", str(ORCH), "factory", *args, cwd=repo, env=env, **kw)


def start_driver(repo, env, task_id):
    return subprocess.Popen(["uv", "run", "--project", str(ORCH), "factory", "advance", task_id], cwd=repo, env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def main():
    root = Path(tempfile.mkdtemp(prefix="orphan-e2e-"))
    repo, state = root / "repo", root / "state"
    (repo / "target").mkdir(parents=True)
    (repo / "target" / "code.py").write_text("x")
    claude = root / "slow-claude"
    claude.write_text(f"#!/bin/sh\nsleep {SLEEP_AGENT} &\nsleep {SLEEP_AGENT}\n")
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (repo / "factory.toml").write_text(
        f'claude_executable = "{claude}"\nstate_dir = "{state}"\nworktree_root = "{root / "wt"}"\n'
        f'test_argv = ["sh", "-c", "sleep {SLEEP_GATE} & sleep {SLEEP_GATE}"]\ngate_timeout_s = 600\nrunner_timeout_s = 600\n')
    for cmd in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "init")):
        sh("git", *cmd, cwd=repo, env=env)
    sys.path.insert(0, str(ORCH / "src"))
    from factory.store import TaskStore
    from factory.task import Task, TaskState
    (state / "tasks").mkdir(parents=True)
    store = TaskStore(state / "tasks")
    base = sh("git", "rev-parse", "HEAD", cwd=repo, env=env).stdout.strip()
    store.save(Task(id="a1", prompt="implement it", state=TaskState.QUEUED))

    print("--- (a) nono-wrapped agent, driver SIGKILLed ---")
    driver = start_driver(repo, env, "a1")
    check("agent started", wait_for(lambda: sleepers(SLEEP_AGENT)))
    records = list((state / "procs").glob("*.json")) if (state / "procs").exists() else []
    check("a record exists while the agent runs", len(records) == 1, str([r.name for r in records]))
    if records:
        print("   record:", json.loads(records[0].read_text()))
    os.kill(driver.pid, signal.SIGKILL)
    driver.wait()
    time.sleep(1)
    check("the agent tree survives the SIGKILL (the orphan)", bool(sleepers(SLEEP_AGENT)), str(sleepers(SLEEP_AGENT)))
    recovered = factory(repo, env, "recover", "a1")
    print("   recover stdout:", recovered.stdout.strip().replace("\n", " | "))
    check("recover exits 0 and reports a killed orphan", recovered.returncode == 0 and "killed orphan of a1" in recovered.stdout, recovered.stderr.strip())
    check("every process of the agent tree is gone", wait_for(lambda: not sleepers(SLEEP_AGENT), 10), str(sleepers(SLEEP_AGENT)))
    check("the record is gone", not list((state / "procs").glob("*.json")))

    print("--- (b) gate test run, driver SIGKILLed ---")
    (repo / "target" / "code.py").write_text("changed by the implementer")
    store.save(Task(id="b1", prompt="stored", state=TaskState.AGENT_DONE, worktree=str(repo), base_sha=base, goal="g", plan="p"))
    driver = start_driver(repo, env, "b1")
    check("gate test run started", wait_for(lambda: sleepers(SLEEP_GATE)))
    records = list((state / "procs").glob("*.json"))
    check("a gate record exists while the tests run", len(records) == 1 and json.loads(records[0].read_text())["stage"] == "gate")
    os.kill(driver.pid, signal.SIGKILL)
    driver.wait()
    time.sleep(1)
    check("the gate tree survives the SIGKILL (the orphan)", bool(sleepers(SLEEP_GATE)), str(sleepers(SLEEP_GATE)))
    abandoned = factory(repo, env, "abandon", "b1")
    print("   abandon stdout:", abandoned.stdout.strip().replace("\n", " | "), "| stderr:", abandoned.stderr.strip())
    check("abandon reports a killed gate orphan", "killed orphan of b1 (gate)" in abandoned.stdout)
    check("every process of the gate tree is gone", wait_for(lambda: not sleepers(SLEEP_GATE), 10), str(sleepers(SLEEP_GATE)))

    print("--- (c) a probe that cannot run ps fails closed ---")
    from factory.process_registry import Record, write_record
    write_record(state, Record(99999, 99999, "c1", "gate", time.time() - 5, time.time() - 4))
    blocked_env = {**env, "PATH": str(root / "nobin")}
    (root / "nobin").mkdir()
    (root / "nobin" / "uv").symlink_to(subprocess.run(["which", "uv"], capture_output=True, text=True).stdout.strip())
    refused = sh(str(root / "nobin" / "uv"), "run", "--project", str(ORCH), "factory", "abandon", "a1", cwd=repo, env=blocked_env)
    print("   stderr:", refused.stderr.strip())
    check("with ps unreachable the command exits 1 saying nothing changed", refused.returncode == 1 and "nothing changed" in refused.stderr)
    check("and the record is kept", (state / "procs" / "99999.json").exists())

    for marker in (SLEEP_AGENT, SLEEP_GATE):
        subprocess.run(["pkill", "-f", f"sleep {marker}"])
    print(f"\n{sum(results)}/{len(results)} PASS   (temp dir {root})")
    sys.exit(0 if all(results) else 1)


main()
