import pytest

from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

EVENTS = "/ui/tasks/20260102-030405/events"


@pytest.fixture
def task(ctx):
    add(ctx)


@pytest.mark.parametrize("headers", [{"Origin": "http://evil.example"}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}],
                         ids=["foreign-origin", "non-loopback-host", "cross-site"])
def test_guard_a_foreign_origin_host_or_site_is_refused_on_the_task_stream_with_the_security_headers(client, task, headers):
    response = client.get(EVENTS, headers=headers)

    assert response.status_code == 403
    assert_ui_security_headers(response)
