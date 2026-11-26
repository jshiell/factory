import subprocess

import pytest

from test_web_clean import post
from webhelpers import assert_ui_security_headers

pytest_plugins = ["webfixtures"]


@pytest.fixture
def git_fails(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "branch", "--list", "secret-ref"], stderr="fatal: secret")

    monkeypatch.setattr("factory.server.clean_candidates", fail)


def _assert_git_refusal(response):
    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert "Git could not check" in response.text
    for leaked in ("Traceback", "CalledProcessError", "secret-ref", "fatal"):
        assert leaked not in response.text


def test_the_clean_page_refuses_when_git_cannot_check_the_repository(client, git_fails):
    _assert_git_refusal(client.get("/ui/repos/proj/clean"))


def test_posting_a_clean_refuses_when_git_cannot_check_the_repository_and_queues_no_job(client, git_fails):
    _assert_git_refusal(post(client, {"token": "0" * 64}))

    assert client.app.state.repos["proj"].jobs.list() == []
