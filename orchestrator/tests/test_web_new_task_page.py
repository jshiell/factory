from dataclasses import replace

from webhelpers import assert_ui_security_headers

pytest_plugins = ["webfixtures"]


def test_the_new_task_form_has_a_goal_textarea_and_a_grill_checkbox_checked_by_default(client):
    response = client.get("/ui/tasks/new")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert '<form method="post" action="/ui/tasks/new">' in response.text
    assert '<textarea name="goal"' in response.text
    assert '<input type="checkbox" name="grill" value="true" checked>' in response.text


def test_the_grill_checkbox_is_unchecked_when_the_setting_is_off(client):
    client.app.state.repos["proj"].settings = replace(client.app.state.repos["proj"].settings, grill=False)

    response = client.get("/ui/tasks/new")

    assert '<input type="checkbox" name="grill" value="true">' in response.text
    assert "checked" not in response.text
