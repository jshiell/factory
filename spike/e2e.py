"""THROWAWAY (plan 2 cycle H): one real task end to end. Run:
uv run --project orchestrator python spike/e2e.py
Costs real money (budget_usd below, soft cap)."""
import time
from pathlib import Path

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.run_task import run_task
from factory.runner import SubprocessRunner
from factory.store import TaskStore
from factory.task import Task
from factory.verify_task import verify_task
from factory.worktrees import WorktreeManager

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "spike" / "e2e-state"
(STATE / "tasks").mkdir(parents=True, exist_ok=True)

task_id = f"e2e-{int(time.time())}"
store = TaskStore(STATE / "tasks")
ledger = Ledger(STATE / "ledger.jsonl")
worktrees = WorktreeManager(REPO, REPO / ".worktrees")
store.save(Task(id=task_id, prompt=(
    "In this project, add a function subtract(a, b) to src/calc/__init__.py with a test in "
    "tests/test_calc.py. Write the failing test first, then the implementation. "
    "Run the tests with `uv run pytest -q`. Do not use git; do not commit.")))
config = RunnerConfig(
    model="sonnet", budget_usd=0.5, permission_mode="acceptEdits",
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash(uv run pytest*)"),
    disallowed_tools=("Bash(git *)",), max_turns=30,
    append_system_prompt="You are in a git worktree; the project lives in the current directory. Do not commit.",
)
runner = SubprocessRunner("claude", project_subdir="target", timeout_s=300)

task = run_task(task_id, store, ledger, runner, config, cap_usd=5.0, worktrees=worktrees)
print("after run:", task.state.value, task.failure_reason, "denials:", len(task.denials))
if task.state.value == "agent_done":
    task = verify_task(task_id, store, project_subdir="target", test_argv=["uv", "run", "pytest", "-q"], gate_timeout_s=120)
    print("after verify:", task.state.value, task.failure_reason)
print("ledger:", ledger.entries())
print("branch: factory/" + task_id, "worktree:", task.worktree)
