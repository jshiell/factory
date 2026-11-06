"""THROWAWAY spike: run one headless agent in a fresh worktree and dump the raw result."""
import json, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TASK = sys.argv[1] if len(sys.argv) > 1 else (
    "In target/, add a subtract(a, b) function to src/calc/__init__.py with a test "
    "in tests/test_calc.py. Run the tests with `cd target && uv run pytest -q`.")
SCHEMA = {"type": "object", "properties": {
    "summary": {"type": "string"}, "tests_passed": {"type": "boolean"}},
    "required": ["summary", "tests_passed"]}

name = f"spike-{int(time.time())}"
wt = REPO / ".worktrees" / name
subprocess.run(["git", "worktree", "add", "-q", "-b", name, str(wt)], cwd=REPO, check=True)

cmd = ["claude", "-p", TASK, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
       "--max-budget-usd", "0.50", "--model", "sonnet", "--permission-mode", "acceptEdits",
       "--allowedTools", "Read Edit Write Bash(uv run pytest*) Bash(cd target && uv run pytest*)"]
start = time.time()
proc = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, stdin=subprocess.DEVNULL)
print(f"exit={proc.returncode} elapsed={time.time()-start:.1f}s worktree={wt}")
(REPO / "spike" / f"{name}.stdout.json").write_text(proc.stdout)
(REPO / "spike" / f"{name}.stderr.txt").write_text(proc.stderr)
print(proc.stdout[:3000]); print("STDERR:", proc.stderr[:1000])
