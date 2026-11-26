import pytest
from starlette.testclient import TestClient

from factory.repos import RepoRegistry
from factory.server import _build_repos_app
from factory.task import Task, TaskState
from test_server_repos import make_ctx


@pytest.fixture
def contexts(tmp_path):
    return {name: make_ctx(tmp_path, name) for name in ("a", "b")}


@pytest.fixture
def client(contexts):
    with TestClient(_build_repos_app(RepoRegistry(contexts, {})), base_url="http://127.0.0.1:8765") as client:
        yield client


def test_the_index_lists_the_tasks_of_every_repo_each_linked_under_its_repo(client, contexts):
    contexts["a"].store.save(Task(id="in-a", prompt="goal a", state=TaskState.QUEUED))
    contexts["b"].store.save(Task(id="in-b", prompt="goal b", state=TaskState.QUEUED))

    page = client.get("/ui/")

    assert page.status_code == 200
    assert 'href="/ui/repos/a/tasks/in-a"' in page.text and "goal a" in page.text
    assert 'href="/ui/repos/b/tasks/in-b"' in page.text and "goal b" in page.text
