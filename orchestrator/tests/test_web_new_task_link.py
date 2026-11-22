pytest_plugins = ["webfixtures"]


def test_the_task_list_page_links_to_the_new_task_form_outside_the_part_the_stream_replaces(client):
    link = '<a href="/ui/tasks/new">New task</a>'

    page = client.get("/ui/").text

    assert page.count(link) == 1
    assert page.index(link) < page.index('id="tasks"')
