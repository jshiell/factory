from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src" / "factory"


def test_neither_the_driver_nor_the_stage_wiring_references_rebase_task():
    for name in ("advance.py", "stages.py"):
        assert "rebase_task" not in (SOURCE / name).read_text(), f"{name} references rebase_task"
