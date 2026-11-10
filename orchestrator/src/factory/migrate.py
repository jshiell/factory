from typing import NamedTuple


class MigrationReport(NamedTuple):
    tasks: int
    ledger_entries: int
    skipped: list[str]


def migrate(old_store, old_ledger, new_store, new_ledger) -> MigrationReport:
    """Copy the file-backed tasks and ledger into the SQLite ones, leaving the old files alone."""
    listing = old_store.list()
    for task in listing.tasks:
        new_store.save(task)
    entries = old_ledger.entries()
    for entry in entries:
        new_ledger.append(entry["task_id"], entry["cost_usd"], entry.get("assumed", False), entry.get("stage"))
    return MigrationReport(len(listing.tasks), len(entries), listing.skipped)
