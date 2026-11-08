"""THROWAWAY (plan 22). Measure the auditor. Usage (from repo root):
    uv run --project orchestrator python spike/audit_eval.py check-gate [--out DIR]
Later subcommands (added in later cycles): run.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audit_cases import CASES, Case  # noqa: E402
from factory.gate import check_gate, commit_all  # noqa: E402
from factory.worktrees import WorktreeManager, run_git  # noqa: E402

TARGET = HERE.parent / "target"
PROJECT_SUBDIR = "target"
TEST_ARGV = ["uv", "run", "pytest", "-q"]
IDENTITY = ("-c", "user.name=factory", "-c", "user.email=factory@localhost", "-c", "commit.gpgsign=false")


def build_base_repo(root: Path) -> Path:
    repo = root / "base"
    project = repo / PROJECT_SUBDIR
    for rel in ("pyproject.toml", "uv.lock", "src/calc/__init__.py", "tests/test_calc.py"):
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(TARGET / rel, project / rel)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".venv/\n__pycache__/\n.pytest_cache/\n*.egg-info/\n")
    run_git(repo, "add", "-A")
    run_git(repo, *IDENTITY, "commit", "-q", "-m", "base")
    return repo


def materialise(case: Case, manager: WorktreeManager, task_id: str) -> tuple[Path, str]:
    """A fresh worktree with the case's after-state committed on top of the shared base."""
    worktree, base_sha = manager.create(task_id)
    for rel, content in case.files.items():
        path = worktree / PROJECT_SUBDIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    commit_all(worktree, "change")
    return worktree, base_sha


def diff_size(worktree: Path, base_sha: str) -> int:
    diff = run_git(worktree, "diff", "--no-ext-diff", "--no-textconv", base_sha, "HEAD", "--", PROJECT_SUBDIR)
    return len(diff.encode())


def check_gate_cmd(out: Path | None) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = WorktreeManager(build_base_repo(root), root / "wt")
        rows = []
        for case in CASES:
            worktree, base_sha = materialise(case, manager, f"gate-{case.id}")
            result = check_gate(worktree, base_sha, PROJECT_SUBDIR, TEST_ARGV, 120.0)
            rows.append((case, "ok" if result.passed else f"BLOCKED {result.reason}", diff_size(worktree, base_sha)))
            print(f"{case.id} {case.label:4} {case.category:24} gate={rows[-1][1]:24} diff={rows[-1][2]}B", flush=True)
            if not result.passed and result.output:
                print("    " + result.output.strip().replace("\n", "\n    "))
        bad = [case.id for case, gate, _ in rows if gate != "ok"]
        print(f"\n{len(rows)} cases, pass={sum(c.label == 'pass' for c, _, _ in rows)}, "
              f"fail={sum(c.label == 'fail' for c, _, _ in rows)}, gate-blocked={bad or 'none'}")
        return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check-gate")
    check.add_argument("--out", type=Path)
    args = parser.parse_args()
    return check_gate_cmd(args.out) if args.cmd == "check-gate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
