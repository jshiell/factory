import re
from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src" / "factory"

# Every module that starts a process, and why it is allowed to. A new entry needs a decision: is the new
# process confined (sandbox prefix) or one of the factory's own trusted commands?
ALLOWED = {
    "runner.py": "agent runs: confined by the configured sandbox's prefix (tests in test_runner.py)",
    "process.py": "the gate's test run (confined by the prefix from gate_sandbox, tests in test_gate.py) and pgrep",
    "worktrees.py": "the factory's own git, with the hardened prefix",
    "gate_sandbox.py": "trusted `uv python find/dir` run by the factory, never agent code",
    "clone_repo.py": "`gh repo clone` of a GitHub repository into a destination that does not exist yet; no worktree, no agent code",
}


def test_only_the_known_modules_start_processes():
    users = sorted(path.name for path in SOURCE.rglob("*.py") if re.search(r"subprocess\.(run|Popen)\(", path.read_text()))

    assert users == sorted(ALLOWED)
