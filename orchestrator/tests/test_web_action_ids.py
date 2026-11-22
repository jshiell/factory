"""Guard: the shared action handlers refuse a missing, invalid or unreadable id without echoing it, for every action
that test_web_abandon_ids does not already cover."""
import pytest

from test_web_reject_ids import (
    REFUSED_IDS,
    _assert_refusal_without_the_id,
    _plant_unreadable_task,
)
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

FORM = "application/x-www-form-urlencoded"
ACTIONS = ["recover", "advance", "rebase"]


def _post(client, action, task_id):
    return client.post(f"/ui/tasks/{task_id}/{action}", content=b"", follow_redirects=False,
                       headers={**same_origin_headers(), "Content-Type": FORM})


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_the_page_of_a_missing_or_invalid_id_is_a_404_without_the_id(client, action, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/tasks/{task_id}/{action}"), 404, task_id)


@pytest.mark.parametrize("action", ACTIONS)
def test_the_page_of_an_unreadable_task_is_a_500_without_the_id(client, ctx, action):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(client.get(f"/ui/tasks/broken-one/{action}"), 500, "broken-one")


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_posting_for_a_missing_or_invalid_id_is_a_404_without_the_id_and_queues_no_job(client, action, task_id):
    _assert_refusal_without_the_id(_post(client, action, task_id), 404, task_id)
    assert client.app.state.jobs.list() == []


@pytest.mark.parametrize("action", ACTIONS)
def test_posting_for_an_unreadable_task_is_a_500_without_the_id_and_queues_no_job(client, ctx, action):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(_post(client, action, "broken-one"), 500, "broken-one")
    assert client.app.state.jobs.list() == []
