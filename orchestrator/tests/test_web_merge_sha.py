"""The merge POST checks the shown audited_sha synchronously: anything but the task's exact audited sha queues nothing."""
import pytest

from gitutil import git
from test_web_merge import TASK_ID, post_merge, sha_form
from webhelpers import assert_ui_security_headers

pytest_plugins = ["webfixtures", "webmerge"]

STALE_SHAS = {
    "another sha": lambda sha: "0" * 40,
    "short prefix": lambda sha: sha[:7],
    "upper case": lambda sha: sha.upper(),
    "leading space": lambda sha: " " + sha,
    "trailing space": lambda sha: sha + " ",
    "trailing newline": lambda sha: sha + "\n",
    "trailing nul": lambda sha: sha + "\x00",
    "doubled": lambda sha: sha + sha,
    "ref HEAD": lambda sha: "HEAD",
    "ref of the branch": lambda sha: f"factory/{TASK_ID}",
    "option": lambda sha: "--upload-pack=touch /tmp/pwned",
    "markup": lambda sha: "<script>alert(1)</script>",
    "non-ASCII": lambda sha: "é" + sha,
    "long": lambda sha: "a" * 4000,
}


def _assert_nothing_queued_or_merged(client, ctx, audited, repo):
    assert client.app.state.jobs.list() == []
    assert ctx.store.load(TASK_ID) == audited
    assert git(repo, "rev-parse", "HEAD") == audited.base_sha


@pytest.mark.parametrize("make_sha", STALE_SHAS.values(), ids=STALE_SHAS.keys())
def test_posting_any_sha_but_the_audited_one_is_a_409_stale_page_that_queues_nothing(client, ctx, audited, repo, make_sha):
    shown = make_sha(audited.audited_sha)

    response = post_merge(client, sha_form(shown))

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert "changed since you opened this page" in response.text
    assert "<form" not in response.text and '<a href="/ui/">' in response.text
    for echoed in (shown, "<script>", "pwned", TASK_ID, audited.audited_sha[:7]):
        assert echoed not in response.text
    _assert_nothing_queued_or_merged(client, ctx, audited, repo)


BAD_SHA_FIELDS = {
    "missing": lambda sha: b"",
    "another field only": lambda sha: b"sha=" + sha.encode(),
    "blank": lambda sha: b"audited_sha=",
    "whitespace": lambda sha: b"audited_sha=%20%09",
    "repeated, same value": lambda sha: f"audited_sha={sha}&audited_sha={sha}".encode(),
    "repeated, other first": lambda sha: f"audited_sha=x&audited_sha={sha}".encode(),
}


@pytest.mark.parametrize("make_body", BAD_SHA_FIELDS.values(), ids=BAD_SHA_FIELDS.keys())
def test_a_missing_blank_or_repeated_audited_sha_is_a_422_that_queues_nothing(client, ctx, audited, repo, make_body):
    response = post_merge(client, make_body(audited.audited_sha))

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert "<form" not in response.text
    assert audited.audited_sha not in response.text and TASK_ID not in response.text
    _assert_nothing_queued_or_merged(client, ctx, audited, repo)
