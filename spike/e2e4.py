"""THROWAWAY (plan 4 cycle H): goal -> plan -> review -> HUMAN approval -> implement -> verify -> audit.
Run in your own terminal (it asks for approval on stdin; never auto-approves):
  uv run --project orchestrator python spike/e2e4.py
Costs real money (soft caps: ~$0.30 plan, review, audit; $0.50 implement)."""
import time
from pathlib import Path

from factory.approve_plan import approve_plan, reject_plan
from factory.audit_task import AUDIT_CONFIG, audit_task
from factory.command import RunnerConfig
from factory.digest import plan_digest
from factory.ledger import Ledger
from factory.plan_task import PLAN_CONFIG, plan_task
from factory.review_plan import REVIEW_CONFIG, review_plan
from factory.run_task import run_task
from factory.runner import SubprocessRunner
from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.verify_task import verify_task
from factory.worktrees import WorktreeManager

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "spike" / "e2e4-state"
(STATE / "tasks").mkdir(parents=True, exist_ok=True)

task_id = f"e2e4-{int(time.time())}"
store = TaskStore(STATE / "tasks")
ledger = Ledger(STATE / "ledger.jsonl")
worktrees = WorktreeManager(REPO, REPO / ".worktrees")
runner = SubprocessRunner("claude", project_subdir="target", timeout_s=300)
store.save(Task(id=task_id, prompt="", state=TaskState.PROPOSED,
                goal="Add a function multiply(a, b) to src/calc/__init__.py with tests in tests/test_calc.py."))

def show(task):
    print(f"[{task.state.value}] failure={task.failure_reason} plan_error={task.plan_error} review_error={task.review_error}")
    return task

task = show(plan_task(task_id, store, ledger, runner, PLAN_CONFIG, cap_usd=5.0, worktrees=worktrees))
if task.state is TaskState.PLANNED:
    task = show(review_plan(task_id, store, ledger, runner, REVIEW_CONFIG, cap_usd=5.0))
if task.state is not TaskState.REVIEWED:
    print("findings:", task.review_findings); raise SystemExit("stopped before approval")

print("\n--- PLAN ---\n" + task.plan + "\n--- RISKS ---\n" + "\n".join(task.plan_risks)
      + "\n--- REVIEW FINDINGS ---\n" + "\n".join(task.review_findings))
digest = plan_digest(task.plan)  # hash of exactly the text printed above
if input("\nApprove this plan? [y/N] ").strip().lower() != "y":
    show(reject_plan(task_id, store)); raise SystemExit("rejected")
show(approve_plan(task_id, store, digest))

config = RunnerConfig(
    model="sonnet", budget_usd=0.5, permission_mode="acceptEdits",
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash(uv run pytest*)"),
    disallowed_tools=("Bash(git *)",), max_turns=30,
    append_system_prompt="You are in a git worktree; the project lives in the current directory. Do not commit.",
)
task = show(run_task(task_id, store, ledger, runner, config, cap_usd=5.0, worktrees=worktrees))
if task.state is TaskState.AGENT_DONE:
    task = show(verify_task(task_id, store, project_subdir="target", test_argv=["uv", "run", "pytest", "-q"], gate_timeout_s=120))
if task.state is TaskState.VERIFIED:
    task = show(audit_task(task_id, store, ledger, runner, AUDIT_CONFIG, cap_usd=5.0, project_subdir="target"))
    print("audit findings:", task.audit_findings, "audit_error:", task.audit_error)
print("ledger:", ledger.entries())
print("branch: factory/" + task_id, "worktree:", task.worktree)
