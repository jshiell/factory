import logging

import pytest

from factory.sqlite_store import SqliteTaskStore
from factory.store import TaskExists
from factory.task import Task


def test_listener_is_called_after_save(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    calls = []
    store.subscribe(lambda: calls.append("save"))

    store.save(Task(id="t1", prompt="p"))

    assert calls == ["save"]


def test_listener_is_called_after_create(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    calls = []
    store.subscribe(lambda: calls.append("create"))

    store.create(Task(id="t1", prompt="p"))

    assert calls == ["create"]


def test_listener_is_not_called_when_create_raises_task_exists(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    store.create(Task(id="t1", prompt="p"))
    calls = []
    store.subscribe(lambda: calls.append("create"))

    with pytest.raises(TaskExists):
        store.create(Task(id="t1", prompt="again"))

    assert calls == []


def test_listener_sees_the_committed_row(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    seen = []
    store.subscribe(lambda: seen.append(store.load("t1").prompt))

    store.save(Task(id="t1", prompt="p"))

    assert seen == ["p"]


def test_raising_listener_is_logged_and_does_not_fail_the_save(tmp_path, caplog):
    store = SqliteTaskStore(tmp_path / "factory.db")
    later = []

    def broken():
        raise RuntimeError("boom")

    store.subscribe(broken)
    store.subscribe(lambda: later.append("ran"))

    with caplog.at_level(logging.WARNING):
        store.save(Task(id="t1", prompt="p"))

    assert store.load("t1").prompt == "p"
    assert later == ["ran"]
    assert any(r.levelno == logging.WARNING and "boom" in r.getMessage() + str(r.exc_info) for r in caplog.records)
