import re
from urllib.parse import urlencode

import pytest

from factory.clean_tasks import Candidate, Outcome
from factory.gate_cache import OrphanCache, OrphanScan
from factory.task import Task, TaskState
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

FORM = "application/x-www-form-urlencoded"


@pytest.fixture
def candidates(monkeypatch):
    found = [Candidate(Task(id="20260102-030405", prompt="", state=TaskState.FAILED, worktree="/w/t1"),
                       directory_exists=True, dirty=True, commits_ahead=2, branch_exists=True)]
    monkeypatch.setattr("factory.server.clean_candidates", lambda *a, **k: found)
    return found


@pytest.fixture
def cleaned(monkeypatch):
    calls = []
    monkeypatch.setattr("factory.server.clean_tasks",
                        lambda *a, **k: calls.append(a) or [Outcome("20260102-030405", done=["removed worktree"])])
    return calls


def token_of(page):
    return re.search(r'name="token" value="([0-9a-f]{64})"', page).group(1)


def post(client, fields):
    return client.post("/ui/repos/proj/clean", content=urlencode(fields).encode(),
                       headers={**same_origin_headers(), "Content-Type": FORM}, follow_redirects=False)


def test_the_clean_page_says_when_there_is_nothing_to_clean_and_offers_no_button(client):
    page = client.get("/ui/repos/proj/clean").text

    assert "Nothing to clean" in page
    assert "<form" not in page


def test_the_clean_page_lists_each_candidate_with_its_flags_and_asks_for_confirmation(client, candidates):
    page = client.get("/ui/repos/proj/clean").text

    assert '<a href="/ui/repos/proj/tasks/20260102-030405">20260102-030405</a>' in page
    for text in ("Failed", "dirty", "2 commits ahead", "Clean up"):
        assert text in page
    assert 'method="post"' in page and 'action="/ui/repos/proj/clean"' in page


def test_the_clean_page_lists_orphan_caches_and_skipped_names_escaped(client, monkeypatch):
    monkeypatch.setattr("factory.server.orphan_caches",
                        lambda *a, **k: OrphanScan([OrphanCache("<b>cache</b>", 2048)], ["<i>skipped</i>"]))

    page = client.get("/ui/repos/proj/clean").text

    assert "&lt;b&gt;cache&lt;/b&gt;" in page and "&lt;i&gt;skipped&lt;/i&gt;" in page
    assert "<b>cache" not in page and "<i>skipped" not in page


def test_confirming_with_the_shown_token_queues_a_clean_job_and_redirects_to_the_jobs_page(client, candidates, cleaned):
    token = token_of(client.get("/ui/repos/proj/clean").text)

    response = post(client, {"token": token})
    client.app.state.runner.wait_idle()

    assert response.status_code == 303 and response.headers["location"] == "/ui/jobs"
    assert [job.kind for job in client.app.state.repos["proj"].jobs.list()] == ["clean"]
    assert len(cleaned) == 1


def test_a_token_that_no_longer_matches_is_a_409_and_queues_nothing(client, candidates, cleaned):
    token = token_of(client.get("/ui/repos/proj/clean").text)
    candidates.clear()

    response = post(client, {"token": token})

    assert response.status_code == 409
    assert client.app.state.repos["proj"].jobs.list() == [] and cleaned == []


@pytest.mark.parametrize("fields", [{}, {"other": "x"}, [("token", "a"), ("token", "b")]], ids=["none", "other", "repeated"])
def test_a_form_without_exactly_one_token_is_422_and_queues_nothing(client, candidates, cleaned, fields):
    response = post(client, fields)

    assert response.status_code == 422
    assert client.app.state.repos["proj"].jobs.list() == [] and cleaned == []


def test_the_clean_page_is_linked_from_the_task_list(client):
    assert '<a href="/ui/repos/proj/clean">' in client.get("/ui/").text


def test_clean_without_a_session_is_401_and_a_foreign_origin_post_is_403(client, candidates, cleaned):
    foreign = client.post("/ui/repos/proj/clean", content=b"token=x", headers={"Origin": "http://evil.example", "Content-Type": FORM})
    client.cookies.clear()

    assert foreign.status_code == 403
    assert [client.get("/ui/repos/proj/clean").status_code, post(client, {"token": "x"}).status_code] == [401, 401]
    assert client.app.state.repos["proj"].jobs.list() == [] and cleaned == []
