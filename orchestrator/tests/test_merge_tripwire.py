import re
from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src" / "factory"


def _files(*excluded):
    return [path for path in SOURCE.rglob("*.py") if path.name not in excluded]


def test_tripwire_merging_is_only_wired_in_merge_task_and_exactly_once_in_the_cli_and_the_server():
    """A tripwire, not a guarantee: 'human-only' is enforced by process (see plans 4 to 7)."""
    calls = {str(path.relative_to(SOURCE)): len(re.findall(r"merge_task\(", path.read_text()))
             for path in _files("merge_task.py")}
    event_users = [str(path.relative_to(SOURCE)) for path in _files("merge_task.py", "task.py")
                   if "Event.MERGED" in path.read_text()]

    assert {name: count for name, count in calls.items() if count} == {"cli.py": 1, "server.py": 1}
    assert event_users == []


def test_the_driver_never_references_the_merge_functions():
    source = (SOURCE / "advance.py").read_text()

    assert "merge_task" not in source and "merge_preview" not in source and "Event.MERGED" not in source
