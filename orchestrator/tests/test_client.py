import httpx
import pytest

from factory.client import ServerClient, ServerRefused, ServerUnreachable


def client_over(handler, token="tok"):
    return ServerClient(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8765"), token,
                        sleep=lambda seconds: None)


def test_every_request_carries_the_bearer_token():
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"tasks": []})

    assert client_over(handler, "secret").get("/tasks") == {"tasks": []}
    assert seen == ["Bearer secret"]


def test_no_server_listening_is_reported_as_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ServerUnreachable):
        client_over(handler).get("/tasks")


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.WriteTimeout])
def test_a_timeout_is_reported_as_unreachable(failure):
    def handler(request):
        raise failure("timed out")

    with pytest.raises(ServerUnreachable):
        client_over(handler).get("/tasks")


def test_a_refusal_carries_its_status_error_and_detail():
    def handler(request):
        return httpx.Response(409, json={"error": "merge refused", "detail": "stale sha"})

    with pytest.raises(ServerRefused) as refused:
        client_over(handler).post("/tasks/x/merge", {"audited_sha": "a"})

    assert (refused.value.status, refused.value.error, refused.value.detail) == (409, "merge refused", "stale sha")


def test_waiting_for_a_job_polls_until_it_finishes_and_returns_it():
    states = iter(["queued", "running", "succeeded"])

    def handler(request):
        return httpx.Response(200, json={"id": "j1", "state": next(states), "result": {"ok": 1}, "error": None})

    job = client_over(handler).wait("j1")

    assert (job["state"], job["result"]) == ("succeeded", {"ok": 1})
