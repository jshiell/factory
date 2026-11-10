from factory.ledger import Ledger
import pytest

from factory.migrate import DatabaseNotEmpty, migrate
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.store import TaskStore
from factory.task import Task, TaskState


def old_state(tmp_path):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    store = TaskStore(tasks_dir)
    store.save(Task(id="b", prompt="two", state=TaskState.FAILED, audit_findings=("x",)))
    store.save(Task(id="a", prompt="one", denials=({"path": "/p"},)))
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("a", 0.25)
    ledger.append("b", 0.5, assumed=True, stage="audit")
    return store, ledger


def test_migrate_imports_every_task_and_ledger_entry_unchanged(tmp_path):
    old_store, old_ledger = old_state(tmp_path)
    new_store = SqliteTaskStore(tmp_path / "factory.db")
    new_ledger = SqliteLedger(tmp_path / "factory.db")

    report = migrate(old_store, old_ledger, new_store, new_ledger)

    assert new_store.list().tasks == old_store.list().tasks
    assert new_ledger.entries() == old_ledger.entries()
    assert (report.tasks, report.ledger_entries, report.skipped) == (2, 2, [])


def test_a_legacy_ledger_line_without_the_assumed_flag_imports_as_not_assumed(tmp_path):
    (tmp_path / "tasks").mkdir()
    (tmp_path / "ledger.jsonl").write_text('{"task_id": "a", "cost_usd": 0.1}\n')
    new_ledger = SqliteLedger(tmp_path / "factory.db")

    migrate(TaskStore(tmp_path / "tasks"), Ledger(tmp_path / "ledger.jsonl"), SqliteTaskStore(tmp_path / "factory.db"), new_ledger)

    assert new_ledger.entries() == [{"task_id": "a", "cost_usd": 0.1, "assumed": False}]


@pytest.mark.parametrize("already", ["task", "ledger"])
def test_migrate_refuses_a_database_that_already_has_data_and_changes_nothing(tmp_path, already):
    old_store, old_ledger = old_state(tmp_path)
    new_store = SqliteTaskStore(tmp_path / "factory.db")
    new_ledger = SqliteLedger(tmp_path / "factory.db")
    if already == "task":
        new_store.save(Task(id="z", prompt="existing"))
    else:
        new_ledger.append("z", 1.0)

    with pytest.raises(DatabaseNotEmpty):
        migrate(old_store, old_ledger, new_store, new_ledger)

    assert [t.id for t in new_store.list().tasks] == (["z"] if already == "task" else [])
    assert len(new_ledger.entries()) == (1 if already == "ledger" else 0)


def test_guard_unreadable_task_files_are_reported_and_the_old_files_stay(tmp_path):
    # guard (green on arrival): the file store's list already skips and names them
    old_store, old_ledger = old_state(tmp_path)
    (tmp_path / "tasks" / "corrupt.json").write_text("{not json")
    new_store = SqliteTaskStore(tmp_path / "factory.db")

    report = migrate(old_store, old_ledger, new_store, SqliteLedger(tmp_path / "factory.db"))

    assert report.skipped == ["corrupt.json"] and report.tasks == 2
    assert sorted(p.name for p in (tmp_path / "tasks").iterdir()) == ["a.json", "b.json", "corrupt.json"]
    assert (tmp_path / "ledger.jsonl").exists()
