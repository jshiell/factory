import httpx2
import pytest

from factory.cli.base import NoRepo, task_client
from factory.client import ServerClient
from factory.ids import InvalidTaskId


def make_client():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx2.Response(200, json={})

    http = httpx2.Client(transport=httpx2.MockTransport(handler), base_url="http://127.0.0.1:1")
    return ServerClient(http, "tok"), seen


def test_a_qualified_ref_picks_its_repo_and_the_bare_id():
    server, seen = make_client()

    client, task_id = task_client(server, "other", "proj/20260102-030405")
    client.get(f"/tasks/{task_id}")

    assert (task_id, seen) == ("20260102-030405", ["/repos/proj/tasks/20260102-030405"])


def test_a_bare_id_uses_the_default_repo():
    server, seen = make_client()

    client, task_id = task_client(server, "dflt", "t1")
    client.get(f"/tasks/{task_id}")

    assert (task_id, seen) == ("t1", ["/repos/dflt/tasks/t1"])


def test_a_bare_id_without_a_default_repo_names_the_option():
    server, _ = make_client()

    with pytest.raises(NoRepo, match="--repo"):
        task_client(server, None, "t1")


@pytest.mark.parametrize("ref", ["a b/t1", "_server/t1", "proj/", "/t1", "proj/a/b"])
def test_a_malformed_ref_is_refused(ref):
    server, _ = make_client()

    with pytest.raises(InvalidTaskId):
        task_client(server, "dflt", ref)
