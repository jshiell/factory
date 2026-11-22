"""The guard refuses a request that repeats a header it decides on: which copy counts would otherwise be ambiguous."""
import pytest
from starlette.testclient import TestClient

from factory.server import create_secured_app, load_or_create_token
from test_server_allowed_hosts import make_ctx

OWN = "127.0.0.1:8765"


@pytest.fixture
def served_client(tmp_path):
    ctx = make_ctx(tmp_path, ())
    token = load_or_create_token(ctx.settings.state_dir)
    with TestClient(create_secured_app(ctx, token), base_url=f"http://{OWN}") as client:
        yield client, token


def get(client, path, *headers):
    return client.get(path, headers=list(headers)).status_code


def test_a_repeated_host_is_403_even_when_both_copies_are_loopback(served_client):
    client, token = served_client
    assert get(client, "/tasks", ("Authorization", f"Bearer {token}"), ("Host", OWN), ("Host", OWN)) == 403


@pytest.mark.parametrize("name, value", [("Origin", f"http://{OWN}"), ("Sec-Fetch-Site", "same-origin")])
def test_a_repeated_ui_header_is_403(served_client, name, value):
    client, _ = served_client
    assert get(client, "/ui/login", ("Host", OWN), (name, value), (name, value)) == 403


def test_a_single_copy_of_each_header_is_not_refused(served_client):
    client, token = served_client
    assert get(client, "/tasks", ("Authorization", f"Bearer {token}"), ("Host", OWN)) == 200
    assert get(client, "/ui/login", ("Host", OWN), ("Origin", f"http://{OWN}"), ("Sec-Fetch-Site", "same-origin")) != 403
