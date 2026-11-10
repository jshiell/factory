from factory.ledger import Ledger
from factory.migrate import migrate
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
