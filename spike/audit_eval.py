"""THROWAWAY (plan 22). Measure the auditor. Usage (from repo root):
    uv run --project orchestrator python spike/audit_eval.py check-gate [--out DIR]
    uv run --project orchestrator python spike/audit_eval.py run [--fake] [--runs 3] [--only case-01] [--max-usd 6] [--out DIR]
    uv run --project orchestrator python spike/audit_eval.py report [--out DIR]
`run` is resumable (skips existing result files) and round-robin (run 1 of every case, then run 2, ...).
`--fake` uses canned verdicts, no spend. Results: DIR/<case>-r<run>.json.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audit_cases import CASES, Case  # noqa: E402
from factory.approve_plan import build_implementation_prompt  # noqa: E402
from factory.audit_task import AUDIT_CONFIG, audit_task  # noqa: E402
from factory.gate import check_gate, commit_all  # noqa: E402
from factory.ledger import Ledger  # noqa: E402
from factory.runner import SubprocessRunner  # noqa: E402
from factory.step import BudgetCapReached, RunResult  # noqa: E402
from factory.store import TaskStore  # noqa: E402
from factory.task import Task, TaskState  # noqa: E402
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


DEFAULT_OUT = HERE / "audit-eval-out"


class FakeRunner:
    """Canned verdicts, no spend: correct for most cases, wrong for a few, one timeout, one malformed."""
    WRONG = {"case-03", "case-13"}  # a false fail and a false pass

    def run(self, task, config):
        case = next(c for c in CASES if task.id.endswith(c.id))
        run = int(task.id.split("-")[0].removeprefix("a"))
        if case.id == "case-05" and run == 1:
            return RunResult("", 0, timed_out=True)
        if case.id == "case-06" and run == 1:
            return RunResult("not json", 1)
        verdict = case.label if case.id not in self.WRONG else ("fail" if case.label == "pass" else "pass")
        payload = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.05,
                   "structured_output": {"verdict": verdict, "findings": [f"canned {verdict} for {case.id}"]}}
        return RunResult(json.dumps(payload), 0)


def outcome_of(task: Task) -> str:
    if task.state is TaskState.AUDITED:
        return "pass"
    if task.state is TaskState.FAILED:
        return "fail" if task.failure_reason == "audit_rejected" else str(task.failure_reason)
    if task.state is TaskState.VERIFIED and task.audit_error:
        return f"infra:{task.audit_error}"
    return f"unexpected:{task.state.value}"


def run_one(case: Case, run: int, manager, store, ledger, runner, cap_usd: float, out: Path) -> dict:
    task_id = f"a{run}-{case.id}"
    worktree, base_sha = materialise(case, manager, task_id)
    store.save(Task(id=task_id, prompt=build_implementation_prompt(case.goal, case.plan), state=TaskState.VERIFIED,
                    worktree=str(worktree), base_sha=base_sha, goal=case.goal, plan=case.plan))
    task = audit_task(task_id, store, ledger, runner, AUDIT_CONFIG, cap_usd, PROJECT_SUBDIR)
    spent = [e for e in ledger.entries() if e["task_id"] == task_id]
    record = {
        "case": case.id, "run": run, "label": case.label, "category": case.category,
        "outcome": outcome_of(task), "findings": list(task.audit_findings),
        "cost_usd": round(sum(e["cost_usd"] for e in spent if not e["assumed"]), 4),
        "assumed_usd": round(sum(e["cost_usd"] for e in spent if e["assumed"]), 4),
    }
    (out / f"{case.id}-r{run}.json").write_text(json.dumps(record, indent=1))
    manager.remove(task_id)
    return record


def run_cmd(args) -> int:
    out = args.out or (HERE / "audit-eval-out-fake" if args.fake else DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)
    state = out / "state"
    (state / "tasks").mkdir(parents=True, exist_ok=True)
    store, ledger = TaskStore(state / "tasks"), Ledger(state / "ledger.jsonl")
    runner = FakeRunner() if args.fake else SubprocessRunner("claude", PROJECT_SUBDIR, 300.0)
    cases = [c for c in CASES if not args.only or c.id in args.only]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = WorktreeManager(build_base_repo(root), root / "wt")
        for run in range(1, args.runs + 1):
            for case in cases:
                if (out / f"{case.id}-r{run}.json").exists():
                    continue
                try:
                    record = run_one(case, run, manager, store, ledger, runner, args.max_usd, out)
                except BudgetCapReached:
                    print(f"stopped: budget cap {args.max_usd} reached (ledger total {ledger.total():.2f})")
                    return 1
                print(f"{case.id} r{run} {case.label:4} -> {record['outcome']:28} ${record['cost_usd']:.3f}"
                      f"{' (assumed $%.2f)' % record['assumed_usd'] if record['assumed_usd'] else ''}", flush=True)
    return report_cmd(args)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def report_cmd(args) -> int:
    out = args.out or (HERE / "audit-eval-out-fake" if args.fake else DEFAULT_OUT)
    records = [json.loads(path.read_text()) for path in sorted(out.glob("case-*-r*.json"))]
    by_case: dict[str, list[dict]] = {}
    for record in records:
        by_case.setdefault(record["case"], []).append(record)
    print("\ncase     label category                verdicts (infra shown as ?)   findings of first valid run")
    verdicts: dict[str, list[str]] = {}
    for case in CASES:
        runs = by_case.get(case.id, [])
        valid = [r["outcome"] for r in runs if r["outcome"] in ("pass", "fail")]
        verdicts[case.id] = valid
        shown = " ".join(r["outcome"] if r["outcome"] in ("pass", "fail") else "?" for r in runs)
        first = next((r["findings"] for r in runs if r["outcome"] in ("pass", "fail")), [])
        print(f"{case.id} {case.label:4} {case.category:22} {shown:12} {(first[0][:70] if first else '')}")
    def tally(label: str, wrong: str):
        eligible = [c for c in CASES if c.label == label and len(verdicts[c.id]) >= 2]
        majority = [c for c in eligible if verdicts[c.id].count(wrong) * 2 > len(verdicts[c.id])]
        split = [c for c in eligible if verdicts[c.id].count(wrong) * 2 == len(verdicts[c.id])]
        anywrong = [c for c in eligible if wrong in verdicts[c.id]]
        return eligible, majority, split, anywrong
    for title, label, wrong in (("false-pass (bad judged pass)", "fail", "pass"), ("false-fail (good judged fail)", "pass", "fail")):
        eligible, majority, split, anywrong = tally(label, wrong)
        lo, hi = wilson(len(majority), len(eligible))
        print(f"\n{title}: majority wrong {len(majority)}/{len(eligible)} cases (Wilson 95% {lo:.0%}-{hi:.0%}); "
              f"split {len(split)}; wrong in any run {len(anywrong)}: {[c.id for c in anywrong]}")
    infra = [r for r in records if r["outcome"] not in ("pass", "fail")]
    inconsistent = [c.id for c in CASES if len(set(verdicts[c.id])) > 1]
    print(f"\nruns {len(records)}; non-verdict runs {len(infra)} {[(r['case'], r['run'], r['outcome']) for r in infra]}")
    print(f"inconsistent cases: {inconsistent}")
    print(f"cost (real, non-assumed) ${sum(r['cost_usd'] for r in records):.3f}; assumed ${sum(r['assumed_usd'] for r in records):.2f}")
    print("Case-level intervals: runs of one case are correlated, so n is the number of cases, not runs.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check-gate")
    check.add_argument("--out", type=Path)
    run = sub.add_parser("run")
    run.add_argument("--fake", action="store_true")
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--only", action="append")
    run.add_argument("--max-usd", type=float, default=6.0)
    run.add_argument("--out", type=Path)
    rep = sub.add_parser("report")
    rep.add_argument("--fake", action="store_true")
    rep.add_argument("--out", type=Path)
    args = parser.parse_args()
    return {"check-gate": check_gate_cmd, "run": run_cmd, "report": report_cmd}[args.cmd](args if args.cmd != "check-gate" else args.out)


if __name__ == "__main__":
    raise SystemExit(main())
