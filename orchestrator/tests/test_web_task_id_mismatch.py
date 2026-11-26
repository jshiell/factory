"""A row whose stored id differs from its key is unreadable to `load`, and the detail page says so without echoing the id."""
import json

import pytest

from factory.sqlite_db import transaction
from factory.store import task_to_dict
from factory.task import Task
from test_sqlite_store_id_mismatch import STORED_IDS

pytest_plugins = ["webfixtures"]


def plant(ctx, stored_id):
    data = {**task_to_dict(Task(id="template", prompt="p", goal="g")), "id": stored_id}
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('ok', 'queued', ?)", (json.dumps(data),))


@pytest.mark.parametrize("stored_id", STORED_IDS)
def test_load_raises_for_a_row_whose_stored_id_differs_from_its_key(ctx, stored_id):
    plant(ctx, stored_id)

    with pytest.raises(ValueError):
        ctx.store.load("ok")


@pytest.mark.parametrize("stored_id", STORED_IDS)
def test_the_detail_page_of_such_a_row_is_the_unreadable_page_without_the_stored_id(client, ctx, stored_id):
    plant(ctx, stored_id)

    response = client.get("/ui/repos/proj/tasks/ok")

    assert response.status_code == 500
    assert "That task is unreadable." in response.text
    assert stored_id not in response.text
