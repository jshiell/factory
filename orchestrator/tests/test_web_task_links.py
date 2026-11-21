import pytest

from factory.web.routes import render_tasks
from webhelpers import add

pytest_plugins = ["webfixtures"]


@pytest.mark.parametrize("source", ["page", "fragment"])
def test_each_listed_id_links_to_its_detail_page(client, ctx, source):
    add(ctx, "20260102-030405")
    add(ctx, "20260103-000000")

    html = client.get("/ui/").text if source == "page" else render_tasks(ctx.store)

    for task_id in ("20260102-030405", "20260103-000000"):
        assert f'<a href="/ui/tasks/{task_id}">{task_id}</a>' in html
