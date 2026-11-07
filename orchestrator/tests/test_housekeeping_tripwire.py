from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src" / "factory"
HOUSEKEEPING = ("retry_task", "recover_task", "clean_tasks", "clean_candidates")


def test_neither_the_driver_nor_the_stage_wiring_references_the_housekeeping_commands():
    for name in ("advance.py", "stages.py"):
        source = (SOURCE / name).read_text()
        for word in HOUSEKEEPING:
            assert word not in source, f"{name} references {word}"
