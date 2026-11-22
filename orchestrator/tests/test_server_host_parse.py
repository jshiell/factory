"""The guard reads `Host` strictly: a name, then optionally `:` and ASCII digits. Anything else fails closed."""
import pytest
from starlette.testclient import TestClient

from factory.server import create_secured_app, load_or_create_token
from test_server_allowed_hosts import auth, make_ctx


@pytest.fixture
def served_client(tmp_path):
    ctx = make_ctx(tmp_path, ("nas.lan",))
    token = load_or_create_token(ctx.settings.state_dir)
    with TestClient(create_secured_app(ctx, token), base_url="http://127.0.0.1:8765") as client:
        yield client, token


@pytest.mark.parametrize("host", ["nas.lan:x:y", "nas.lan:", "nas.lan:8765x", "nas.lan:²", "nas.lan:8765:9",
                                  "[::1]x", "[::1]:x:y", "[::1]:", "[::1]:²", "[::1", "127.0.0.1:"])
def test_a_host_with_anything_but_digits_after_the_port_colon_is_403(served_client, host):
    client, token = served_client
    assert client.get("/tasks", headers={**auth(token), "Host": host.encode("latin-1")}).status_code == 403


@pytest.mark.parametrize("host", ["[::1]", "[::1]:8765", "nas.lan:8765", "127.0.0.1:8765", "localhost"])
def test_a_well_formed_host_is_served(served_client, host):
    client, token = served_client
    assert client.get("/tasks", headers={**auth(token), "Host": host}).status_code == 200
