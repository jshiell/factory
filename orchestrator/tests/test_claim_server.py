import pytest

from factory.cli import Context
from factory.job_store import JobStore
from factory.lock import AlreadyRunning, state_lock
from factory.process_registry import ReapResult, ReapUncertain
from factory.server import StartRefused, claim_server
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


def make_ctx(home, name):
    project = home / name
    settings = Settings(repo=project / "repo", state_dir=project / "state", worktree_root=project / "worktrees", home=home)
    settings.state_dir.mkdir(parents=True)
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={})
    context.reaped = []
    context.reap = lambda directory: context.reaped.append(directory) or ReapResult([], [])
    return context


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def test_every_repo_is_claimed_and_started(home):
    contexts = {name: make_ctx(home, name) for name in ("a", "b")}

    with claim_server(home / "_server", contexts, {}) as claimed:
        assert claimed.registry.started_names == ("a", "b")
        assert claimed.registry.get("a") is contexts["a"]
        assert all(len(context.reaped) == 1 for context in contexts.values())


def test_every_lock_is_held_while_serving_and_released_after(home):
    contexts = {"a": make_ctx(home, "a")}

    with claim_server(home / "_server", contexts, {}):
        with pytest.raises(AlreadyRunning), state_lock(contexts["a"].settings.state_dir):
            pass
        with pytest.raises(AlreadyRunning), state_lock(home / "_server"):
            pass

    with state_lock(home / "_server"), state_lock(contexts["a"].settings.state_dir):
        pass


def test_a_second_server_is_refused_and_touches_no_repo(home):
    contexts = {"a": make_ctx(home, "a")}

    with (
        claim_server(home / "_server", contexts, {}),
        pytest.raises(StartRefused, match="another factory server"),
        claim_server(home / "_server", contexts, {}),
    ):
        pass

    assert len(contexts["a"].reaped) == 1


def test_a_repo_whose_lock_is_held_is_skipped_with_the_reason_and_the_others_start(home):
    contexts = {name: make_ctx(home, name) for name in ("busy", "free")}

    with state_lock(contexts["busy"].settings.state_dir), claim_server(home / "_server", contexts, {}) as claimed:
        assert claimed.registry.started_names == ("free",)
        assert "holds the lock" in claimed.registry.skipped["busy"]
    assert contexts["busy"].reaped == []


def test_a_repo_with_uncertain_reaping_is_skipped_and_its_jobs_are_left_alone(home):
    contexts = {name: make_ctx(home, name) for name in ("unsure", "fine")}
    jobs = JobStore(contexts["unsure"].settings.db_path)
    running = jobs.create("advance", "t")
    jobs.mark_running(running.id)

    class Record:
        pid, task_id, stage = 7, "t", "implement"

    def uncertain(directory):
        raise ReapUncertain([], [(Record, "ps: operation not permitted")])

    contexts["unsure"].reap = uncertain

    with claim_server(home / "_server", contexts, {}) as claimed:
        assert claimed.registry.started_names == ("fine",)
        assert "cannot prove these processes dead" in claimed.registry.skipped["unsure"]
    assert jobs.get(running.id).state == "running"


def test_repos_skipped_before_the_claim_are_kept_in_the_registry(home):
    with claim_server(home / "_server", {}, {"bad": "the name is bad"}) as claimed:
        assert claimed.registry.skipped == {"bad": "the name is bad"}


def test_a_server_with_no_repos_starts(home):
    with claim_server(home / "_server", {}, {}) as claimed:
        assert claimed.registry.started_names == ()


def test_the_reaped_processes_are_reported_per_repo(home):
    context = make_ctx(home, "a")
    reaped = ReapResult([], [])
    context.reap = lambda directory: reaped

    with claim_server(home / "_server", {"a": context}, {}) as claimed:
        assert claimed.reaped == {"a": reaped}
