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

    def user_id(self, login):
        return {"alice": 1001, "bob": 1002}[login]


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt", watch_approvers=("alice",))
    settings.state_dir.mkdir()
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={})
    context.origin_slug = lambda repo_dir: ("owner", "name")
    context.github = lambda owner, name: FakeGitHub()
    context.passes = []
    context.pass_calls = []

    def watch_pass(github, server, state_dir, approver_ids):
        context.passes.append(context.settings)
        context.pass_calls.append((github, server, state_dir, approver_ids))

    context.watch_pass = watch_pass
    return context


def watch(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["watch", *argv], IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def test_watch_once_runs_exactly_one_pass_and_exits_0(ctx):
    code, out, err = watch(ctx, "--once")

    assert code == 0
    assert ctx.passes == [ctx.settings]


def test_watch_resolves_approver_logins_to_ids_once_and_hands_the_pass_its_collaborators(ctx):
    ctx.settings = replace(ctx.settings, watch_approvers=("alice", "bob"))
    ctx.server = object()
    github = FakeGitHub()
    ctx.github = lambda owner, name: github

    code, out, err = watch(ctx, "--once")

    assert code == 0
    assert ctx.pass_calls == [(github, ctx.server, ctx.settings.state_dir, {1001, 1002})]


def test_watch_with_an_approver_login_github_cannot_resolve_names_it_and_exits_1_without_a_pass(ctx):
    ctx.settings = replace(ctx.settings, watch_approvers=("alice", "ghost"))

    def lookup(login):
        if login == "ghost":
            raise GitHubError("gh: Not Found (HTTP 404)")
        return 1001

    ctx.github = lambda owner, name: SimpleNamespace(
        self_user=lambda: User("bot", 7), repo_is_private=lambda: True, user_id=lookup
    )

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "ghost" in err
    assert ctx.passes == []


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


def test_watch_when_the_repo_privacy_check_fails_says_so_escaped_and_exits_1_without_a_pass(ctx):
    def privacy_check_fails():
        raise GitHubError("\x1b[31mHTTP 404: Not Found")

    ctx.github = lambda owner, name: SimpleNamespace(self_user=lambda: User("bot", 7), repo_is_private=privacy_check_fails)

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "HTTP 404: Not Found" in err and "\x1b" not in err
    assert ctx.passes == []


def test_watch_with_an_origin_that_is_not_a_plain_owner_and_name_says_so_and_exits_1_without_a_pass(ctx):
    def bad_slug(owner, name):
        raise ValueError("not a plain GitHub owner and name: 'o\\x1b', 'n'")

    ctx.github = bad_slug

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "not a plain GitHub owner and name" in err
    assert ctx.passes == []


def test_watch_on_a_public_repo_with_allow_public_runs_the_pass(ctx):
    ctx.github = lambda owner, name: FakeGitHub(private=False)

    code, out, err = watch(ctx, "--once", "--allow-public")

    assert code == 0
    assert ctx.passes == [ctx.settings]


class StopLoop(Exception):
    pass


def stop_after_sleeps(ctx, count):
    ctx.sleeps = []

    def sleep(seconds):
        ctx.sleeps.append(seconds)
        if len(ctx.sleeps) == count:
            raise StopLoop

    ctx.sleep = sleep


def test_watch_without_once_sleeps_the_interval_after_each_pass(ctx):
    stop_after_sleeps(ctx, 2)

    with pytest.raises(StopLoop):
        watch(ctx, "--interval", "5")

    assert len(ctx.passes) == 2
    assert ctx.sleeps == [5, 5]


def test_watch_stopped_with_ctrl_c_during_the_sleep_exits_0_with_a_short_message(ctx):
    def interrupted(seconds):
        raise KeyboardInterrupt

    ctx.sleep = interrupted

    code, out, err = watch(ctx)

    assert code == 0
    assert "stopped" in err
    assert len(ctx.passes) == 1


@pytest.mark.parametrize("value", ["0", "-5"])
def test_watch_with_a_non_positive_interval_is_a_usage_error_and_runs_nothing(ctx, value):
    stop_after_sleeps(ctx, 1)

    code, out, err = watch(ctx, "--interval", value)

    assert code == 2
    assert "--interval" in err
    assert ctx.passes == []


def test_watch_with_a_non_integer_interval_is_a_usage_error_and_runs_nothing(ctx):
    code, out, err = watch(ctx, "--interval", "soon")

    assert code == 2
    assert ctx.passes == []


def test_watch_runs_the_startup_checks_once_not_per_pass(ctx):
    stop_after_sleeps(ctx, 2)
    lookups = []
    ctx.github = lambda owner, name: lookups.append((owner, name)) or FakeGitHub()

    with pytest.raises(StopLoop):
        watch(ctx)

    assert len(ctx.passes) == 2
    assert len(lookups) == 1


def test_watch_once_never_sleeps(ctx):
    stop_after_sleeps(ctx, 1)

    code, out, err = watch(ctx, "--once")

    assert code == 0
    assert len(ctx.passes) == 1
    assert ctx.sleeps == []
