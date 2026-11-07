"""THROWAWAY e2e for plan 12 (no agents, $0): real git, real gate. Run: uv run --project orchestrator python spike/test_freeze_e2e.py"""
import os, subprocess, sys, tempfile
from pathlib import Path

from factory.gate import check_gate, commit_all

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
results = []


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=ENV, capture_output=True, text=True, check=True).stdout.strip()


def fresh():
    repo = Path(tempfile.mkdtemp()) / "repo"
    (repo / "proj").mkdir(parents=True)
    (repo / "proj" / "code.py").write_text("x\n")
    git(repo, "init", "-q", "-b", "main"); git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "init")
    return repo, git(repo, "rev-parse", "HEAD")


def gate(repo, base, frozen=None):
    return check_gate(repo, base, "proj", ["true"], 30, frozen_sha=frozen)


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"(got {got!r}, want {want!r})")


# implementer adds a test, factory commits it, a "fix round" then edits it
for label, edit in [("rewrite", lambda r: (r / "proj/test_a.py").write_text("def test_a():\n    pass\n")),
                    ("delete", lambda r: (r / "proj/test_a.py").unlink())]:
    repo, base = fresh()
    (repo / "proj/test_a.py").write_text("def test_a():\n    assert False\n")
    commit_all(repo, "Task t1"); frozen = git(repo, "rev-parse", "HEAD")
    edit(repo); (repo / "proj/code.py").write_text("y\n"); commit_all(repo, "Task t1")
    check(f"base-only gate misses a {label}", gate(repo, base).reason, None if label == "rewrite" else None)
    check(f"frozen gate catches a {label}", gate(repo, base, frozen).reason, "tests_modified")

repo, base = fresh()
(repo / "proj/test_a.py").write_text("def test_a():\n    pass\n"); commit_all(repo, "Task t1"); frozen = git(repo, "rev-parse", "HEAD")
(repo / "proj/test_b.py").write_text("def test_b():\n    pass\n"); commit_all(repo, "Task t1")
check("adding a new test file after the freeze passes", gate(repo, base, frozen).passed, True)

for name in ("pytest.toml", ".pytest.toml", ".pytest.ini", "sub/.pytest.toml"):
    repo, base = fresh()
    (repo / "proj" / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / "proj" / name).write_text("[pytest]\n"); (repo / "proj/code.py").write_text("y\n"); commit_all(repo, "Task t1")
    check(f"new config {name} fails", gate(repo, base).reason, "tests_modified")

repo, base = fresh()
(repo / "proj/pyproject.toml").write_text('[tool.pytest]\naddopts = ["-k", "nothing"]\n'); commit_all(repo, "Task t1")
check("[tool.pytest] table fails", gate(repo, base).reason, "tests_modified")

# resume: round edit left uncommitted, fresh gate call after a "crash"
repo, base = fresh()
(repo / "proj/test_a.py").write_text("def test_a():\n    assert False\n"); commit_all(repo, "Task t1"); frozen = git(repo, "rev-parse", "HEAD")
(repo / "proj/test_a.py").write_text("def test_a():\n    pass\n")
commit_all(repo, "Task t1")  # what the next process's first gate run does
check("resumed gate with the stored sha catches the rewrite", gate(repo, base, frozen).reason, "tests_modified")

sys.exit(0 if all(results) else 1)
