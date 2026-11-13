import io
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factory.cli import IO, Context, main
from factory.clone_repo import RepositoryError
from factory.github import GitHubError, User
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


class FakeGitHub:
    def __init__(self, private=True):
        self.private = private

    def self_user(self):
        return User("bot", 7)

    def repo_is_private(self):
        return self.private


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt", watch_approvers=("alice",))
    settings.state_dir.mkdir()
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={})
    context.origin_slug = lambda repo_dir: ("owner", "name")
    context.github = lambda owner, name: FakeGitHub()
    context.passes = []
    context.watch_pass = lambda settings: context.passes.append(settings)
    return context


def watch(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["watch", *argv], IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def test_watch_once_runs_exactly_one_pass_and_exits_0(ctx):
    code, out, err = watch(ctx, "--once")

    assert code == 0
    assert ctx.passes == [ctx.settings]


def test_watch_without_approvers_says_to_set_watch_approvers_and_exits_1_without_a_pass(ctx):
    ctx.settings = replace(ctx.settings, watch_approvers=())

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "watch_approvers" in err
    assert ctx.passes == []


def test_watch_without_gh_installed_says_so_and_exits_1_without_a_pass(ctx):
    def gh_missing():
        raise FileNotFoundError("gh")

    ctx.github = lambda owner, name: SimpleNamespace(self_user=gh_missing)

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "gh is not installed" in err
    assert ctx.passes == []


def test_watch_when_gh_is_not_logged_in_says_to_run_gh_auth_login_and_exits_1_without_a_pass(ctx):
    def not_logged_in():
        raise GitHubError("not logged in")

    ctx.github = lambda owner, name: SimpleNamespace(self_user=not_logged_in)

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "gh auth login" in err
    assert ctx.passes == []


def test_watch_with_an_origin_that_is_not_a_github_repo_says_so_and_exits_1_without_a_pass(ctx):
    def not_github(repo_dir):
        raise RepositoryError("origin is not a GitHub repository: /srv/repo.git")

    ctx.origin_slug = not_github

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "origin is not a GitHub repository" in err
    assert ctx.passes == []


def test_watch_on_a_public_repo_warns_and_exits_1_without_a_pass(ctx):
    ctx.github = lambda owner, name: FakeGitHub(private=False)

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "public" in err
    assert "--allow-public" in err
    assert ctx.passes == []


def test_watch_on_a_public_repo_with_allow_public_runs_the_pass(ctx):
    ctx.github = lambda owner, name: FakeGitHub(private=False)

    code, out, err = watch(ctx, "--once", "--allow-public")

    assert code == 0
    assert ctx.passes == [ctx.settings]
