"""THROWAWAY (plan O7). $0, no API calls. Needs real `nono`, `ps`, `pgrep`, `setsid`, `uv`. Run from the repo root in YOUR
terminal (not inside an agent nono session):
    uv run --project orchestrator python spike/orphan_setsid_probe.py

Replaces `orphan_e2e.py` for this question (that script drives the old file-record flow and `factory advance`; the CLI
now goes through `factory serve`). Per arm: a small driver process runs the real `SubprocessRunner` (confine=True, so
the fake agent is wrapped in nono) with `SqliteProcessRecords`; the probe SIGKILLs the driver while the fake agent's
sleepers are alive, then calls `reap_orphans` with the real `ps_probe` and `kill_tree`, exactly as `claim_state_dir` does.
Arms (the fake agent's script):
  control        `sleep A & sleep B`                     children in the agent's process group
  setsid-parent  `setsid sleep A & sleep B`              a setsid child whose parent shell is still alive at the reap
  setsid-orphan  `(setsid sleep A &); sleep B`           a setsid grandchild whose parent exited before the reap
Prints one line per arm and a SUMMARY. Starts real `sleep 3135x` processes and kills any left at the end."""
import json, os, signal, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "orchestrator"
sys.path.insert(0, str(ORCH / "src"))
from factory.process import group_alive, kill_tree  # noqa: E402
from factory.process_registry import ps_probe, reap_orphans  # noqa: E402
from factory.sqlite_process_records import SqliteProcessRecords  # noqa: E402

ARMS = {
    "control": ("sleep 31351 & sleep 31352", ("31351", "31352")),
    "setsid-parent": ("setsid sleep 31353 & sleep 31354", ("31353", "31354")),
    "setsid-orphan": ("(setsid sleep 31355 &); sleep 31356", ("31355", "31356")),
}
DRIVER = r'''
import sys
from pathlib import Path
from factory.command import RunnerConfig
from factory.runner import SubprocessRunner
from factory.sqlite_process_records import SqliteProcessRecords
from factory.task import Task
exe, worktree, db = sys.argv[1:4]
runner = SubprocessRunner(exe, "proj", 600, records=SqliteProcessRecords(Path(db)))
runner.run(Task(id="t1", prompt="probe", worktree=worktree), RunnerConfig(model="sonnet", confine=True))
'''


def sleepers(marker):
    return subprocess.run(["pgrep", "-f", f"sleep {marker}"], capture_output=True, text=True).stdout.split()


def wait_for(fn, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.3)
    return False


def parent_of(pid):
    return subprocess.run(["ps", "-o", "ppid=,sid=,pgid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def records(db):
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT pid, pgid, task_id, stage FROM processes").fetchall()


def run_arm(name, script, markers, python, base):
    arm = base / name
    (arm / "wt" / "proj").mkdir(parents=True)
    (arm / "exe").mkdir()
    exe, db = arm / "exe" / "fake-claude", arm / "factory.db"
    exe.write_text(f"#!/bin/sh\n{script}\n")
    exe.chmod(0o755)
    (arm / "driver.py").write_text(DRIVER)
    driver = subprocess.Popen([python, str(arm / "driver.py"), str(exe), str(arm / "wt"), str(db)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    out = {"arm": name}
    out["started"] = wait_for(lambda: all(sleepers(m) for m in markers)) and wait_for(lambda: records(db))
    out["record"] = records(db)
    out["ps (ppid, sid, pgid) per sleeper"] = {m: [parent_of(p) for p in sleepers(m)] for m in markers}
    driver.send_signal(signal.SIGKILL)
    driver.wait()
    time.sleep(1)
    out["alive after driver SIGKILL"] = {m: len(sleepers(m)) for m in markers}
    result = reap_orphans(SqliteProcessRecords(db), ps_probe(), kill_tree, group_alive)
    time.sleep(1)
    out["reap killed"] = [r.pid for r in result.killed]
    out["alive after reap"] = {m: len(sleepers(m)) for m in markers}
    out["record left"] = records(db)
    out["verdict"] = "ALL REAPED" if not any(out["alive after reap"].values()) else "SURVIVORS: " + ",".join(m for m in markers if sleepers(m))
    print(f"{name}: {out['verdict']}", flush=True)
    for m in markers:
        for pid in sleepers(m):
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    return out


def main():
    python = subprocess.run(["uv", "run", "--project", str(ORCH), "python", "-c", "import sys; print(sys.executable)"],
                            capture_output=True, text=True, check=True).stdout.strip()
    base = Path(tempfile.mkdtemp(prefix="orphan-setsid-"))
    results = [run_arm(name, script, markers, python, base) for name, (script, markers) in ARMS.items()]
    print("\n================ SUMMARY (plan O7 orphan probe) ================")
    for r in results:
        print(json.dumps(r, indent=1, default=str))
    for _, markers in ARMS.values():
        for m in markers:
            subprocess.run(["pkill", "-f", f"sleep {m}"])
    print(f"temp dir {base}")


main()
