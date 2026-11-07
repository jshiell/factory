from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src" / "factory"
HUMAN_ONLY = ("abandon_task", "orphan_caches", "remove_orphan_caches")


def test_neither_the_driver_nor_the_stage_wiring_references_abandon_or_the_orphan_cache_cleanup():
    for name in ("advance.py", "stages.py"):
        source = (SOURCE / name).read_text()
        for word in HUMAN_ONLY:
            assert word not in source, f"{name} references {word}"
