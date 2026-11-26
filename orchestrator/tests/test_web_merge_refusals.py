"""The merge page's refusals from the git checks in merge_preview: HTML pages, never the app-wide JSON 404/409."""
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from factory.merge_task import MergeRefused
from factory.store import TaskNotFound
from factory.task import InvalidTransition, TaskState
from gitutil import git
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures", "webmerge"]

TASK_ID = "20260102-030405"


def _get(client):
    return client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge")


def _assert_html_refusal(response, status, *shown):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert '<a href="/ui/">' in response.text
    assert "<form" not in response.text and 'name="audited_sha"' not in response.text
    for text in shown:
        assert text in response.text
    for leaked in ("Traceback", "CalledProcessError", "rev-parse", '"error"'):
        assert leaked not in response.text


def test_a_task_main_has_outrun_gets_a_refusal_page_naming_the_reason(client, ctx, audited, repo):
    (repo / "c.txt").write_text("c")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved")

    _assert_html_refusal(_get(client), 409, "cannot be merged", "main moved since the plan")
    assert ctx.store.load(TASK_ID) == audited


def test_a_dirty_worktree_gets_a_refusal_page_naming_the_reason(client, audited):
    (Path(audited.worktree) / "scratch.txt").write_text("x")

    _assert_html_refusal(_get(client), 409, "the worktree is dirty")


def test_a_git_failure_gets_a_refusal_page_without_the_git_command(client, ctx, tmp_path):
    add(ctx, TASK_ID, TaskState.AUDITED, goal="fix the widget",
        worktree=str(tmp_path), base_sha="0" * 40, audited_sha="1" * 40)  # no factory/<id> branch: rev-parse fails

    _assert_html_refusal(_get(client), 409, "Git could not check")


def test_a_task_with_no_worktree_gets_the_git_could_not_check_page(client, ctx, audited):
    ctx.store.save(replace(audited, worktree=None))

    _assert_html_refusal(_get(client), 409, "Git could not check")


def test_a_missing_worktree_directory_gets_a_refusal_page_without_the_path(client, audited):
    shutil.rmtree(audited.worktree)

    response = _get(client)

    _assert_html_refusal(response, 409, "Git could not check")
    assert audited.worktree not in response.text


@pytest.mark.parametrize("error, shown", [
    (InvalidTransition("merged not allowed from verified"), "cannot be merged in its current state"),
    (MergeRefused("<script>alert(1)</script>\x1b[31m"), "&lt;script&gt;alert(1)&lt;/script&gt;\\x1b[31m"),
    (subprocess.CalledProcessError(128, ["git", "rev-parse", "secret-ref"], stderr="fatal: secret"), "Git could not check"),
], ids=["invalid transition", "hostile refusal text", "called process error"])
def test_a_preview_refusal_is_an_escaped_html_409(client, audited, monkeypatch, error, shown):
    def refuse(task_id):
        raise error
    monkeypatch.setattr(client.app.state.repos["proj"].actions, "merge_preview", refuse)

    response = _get(client)

    _assert_html_refusal(response, 409, shown)
    for leaked in ("<script>", "\x1b", "secret", audited.audited_sha):
        assert leaked not in response.text


def test_a_long_refusal_reason_is_capped(client, audited, monkeypatch):
    def refuse(task_id):
        raise MergeRefused("x" * 5000)
    monkeypatch.setattr(client.app.state.repos["proj"].actions, "merge_preview", refuse)

    response = _get(client)

    _assert_html_refusal(response, 409)
    assert "x" * 501 not in response.text


@pytest.mark.parametrize("error, status", [(TaskNotFound(TASK_ID), 404), (ValueError("not json"), 500)],
                         ids=["gone", "unreadable"])
def test_a_task_that_goes_or_breaks_before_the_preview_loads_it_gets_the_task_refusal_page(
        client, audited, monkeypatch, error, status):
    def fail(task_id):
        raise error
    monkeypatch.setattr(client.app.state.repos["proj"].actions, "merge_preview", fail)

    response = _get(client)

    _assert_html_refusal(response, status)
    for leaked in (TASK_ID, "not json"):
        assert leaked not in response.text


def test_a_fix_agent_starting_before_the_preview_loads_the_task_gets_the_wrong_state_page(client, audited, monkeypatch):
    real_preview = client.app.state.repos["proj"].actions.merge_preview

    def preview_after_a_fix_started(task_id):
        preview = real_preview(task_id)
        return replace(preview, task=replace(preview.task, fix_in_flight=True))
    monkeypatch.setattr(client.app.state.repos["proj"].actions, "merge_preview", preview_after_a_fix_started)

    response = _get(client)

    _assert_html_refusal(response, 409, "cannot be merged in its current state")
    assert audited.audited_sha not in response.text
