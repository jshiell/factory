import dataclasses

import httpx2
import pytest

from factory.cli import Context
from test_cli_ui import context, run


def link_to(url):
    def handler(request):
        return httpx2.Response(200, json={"url": url})
    return handler


def listing(ctx, *hosts):
    return Context(**{**vars(ctx), "settings": dataclasses.replace(ctx.settings, server_allowed_hosts=hosts)})


def test_ui_host_swaps_a_listed_name_into_the_link_and_keeps_the_port(tmp_path):
    ctx = listing(context(tmp_path, link_to("http://127.0.0.1:8765/ui/login?code=abc")), "nas.lan")

    assert run(ctx, "ui", "--host", "nas.lan") == (0, "http://nas.lan:8765/ui/login?code=abc\n", "")


def test_ui_host_accepts_a_loopback_name_without_a_list(tmp_path):
    ctx = context(tmp_path, link_to("http://127.0.0.1:8765/ui/login?code=abc"))

    assert run(ctx, "ui", "--host", "localhost") == (0, "http://localhost:8765/ui/login?code=abc\n", "")


def test_ui_host_keeps_a_link_without_a_port_portless(tmp_path):
    ctx = listing(context(tmp_path, link_to("http://127.0.0.1/ui/login?code=abc")), "nas.lan")

    assert run(ctx, "ui", "--host", "nas.lan") == (0, "http://nas.lan/ui/login?code=abc\n", "")


@pytest.mark.parametrize("name", ["evil.example", "NAS.lan", "nas.lan:8765"])
def test_ui_host_refuses_an_unlisted_name_without_posting(tmp_path, name):
    def must_not_post(request):
        raise AssertionError("posted")
    ctx = listing(context(tmp_path, must_not_post), "nas.lan")

    code, out, err = run(ctx, "ui", "--host", name)

    assert (code, out) == (2, "")
    assert err.startswith("usage error:") and name in err and "server_allowed_hosts" in err
    assert "Traceback" not in err
