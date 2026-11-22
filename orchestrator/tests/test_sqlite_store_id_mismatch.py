"""A row whose stored `id` differs from its key is unreadable: the key is what was validated, not the JSON."""
import json
import sqlite3

import pytest

from factory.sqlite_store import SqliteTaskStore
from factory.store import task_to_dict
from factory.task import Task

STORED_IDS = ["other", "ok<b>", "ok\nx", "ok\x07"]


def put_row(db, key, stored_id):
    data = {**task_to_dict(Task(id="template", prompt="p")), "id": stored_id}
    with sqlite3.connect(db) as conn:  # simulates a direct database write; no public way to make one
        conn.execute("INSERT INTO tasks (id, state, data) VALUES (?, 'queued', ?)", (key, json.dumps(data)))


@pytest.mark.parametrize("stored_id", STORED_IDS)
def test_list_skips_a_row_whose_stored_id_differs_from_its_key(tmp_path, stored_id):
    db = tmp_path / "factory.db"
    store = SqliteTaskStore(db)
    store.save(Task(id="fine", prompt="p"))
    put_row(db, "ok", stored_id)

    listing = store.list()

    assert [t.id for t in listing.tasks] == ["fine"]
    assert listing.skipped == ["ok"]
