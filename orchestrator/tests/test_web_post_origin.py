import pytest

from webhelpers import assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

WRITE_METHODS = ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
UI_PATHS = ["/ui", "/ui/", "/ui/login", "/ui/static/ui.js", "/ui/tasks/20260102-030405"]


@pytest.mark.parametrize("path", UI_PATHS)
@pytest.mark.parametrize("method", WRITE_METHODS)
def test_a_write_without_an_origin_is_403_even_with_the_session(client, method, path):
    response = client.request(method, path)

    assert response.status_code == 403
    assert_ui_security_headers(response)


@pytest.mark.parametrize("origin", ["", " http://127.0.0.1:8765", "HTTP://127.0.0.1:8765"])
def test_a_write_with_an_empty_or_inexact_origin_is_403(client, origin):
    assert client.post("/ui/", headers={"Origin": origin}).status_code == 403


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_a_write_with_the_exact_origin_reaches_routing(client, method):
    response = client.request(method, "/ui/", headers=same_origin_headers())

    assert response.status_code == 405
    assert_ui_security_headers(response)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_a_read_without_an_origin_is_still_served(client, method):
    assert client.request(method, "/ui/").status_code == 200
