import pytest

from test_web_reject_ids import (
    REFUSED_IDS,
    _assert_refusal_without_the_id,
    _plant_unreadable_task,
)
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_the_approve_page_of_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/repos/proj/tasks/{task_id}/approve"), 404, task_id)


def test_the_approve_page_of_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(client.get("/ui/repos/proj/tasks/broken-one/approve"), 500, "broken-one")


def _post_approve(client, task_id):
    return client.post(f"/ui/repos/proj/tasks/{task_id}/approve", data={"plan_digest": "x"}, headers=same_origin_headers(), follow_redirects=False)


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_an_approval_posted_to_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(_post_approve(client, task_id), 404, task_id)


def test_an_approval_posted_to_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(_post_approve(client, "broken-one"), 500, "broken-one")
