import pytest

from test_web_reject_ids import (
    REFUSED_IDS,
    _assert_refusal_without_the_id,
    _plant_unreadable_task,
)

pytest_plugins = ["webfixtures"]


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_the_approve_page_of_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/tasks/{task_id}/approve"), 404, task_id)


def test_the_approve_page_of_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(client.get("/ui/tasks/broken-one/approve"), 500, "broken-one")
