import signal
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from factory.audit_corpus import PROJECT_SUBDIR
from factory.client import ServerClient
from factory.harness import harness_named, missing_capabilities
from factory.ledger import Ledger
from factory.process import group_alive, kill_tree
from factory.process_registry import (
    ReapResult,
    ps_probe,
    reap_orphans,
)
from factory.runner import SubprocessRunner
from factory.sandbox import sandbox_named
from factory.settings import GlobalSettings, Settings, SettingsError
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_process_records import SqliteProcessRecords
from factory.sqlite_store import SqliteTaskStore
from factory.step import Runner
from factory.store import TaskStore


@dataclass
class IO:
    stdin: object
    stdout: object
    stderr: object


def _reap_with_the_real_process_table(records) -> ReapResult:
    return reap_orphans(records, ps_probe(), kill_tree, group_alive)


def _real_eval_runner(settings: Settings, eval_dir: Path) -> SubprocessRunner:
    """The eval records its agents under its own directory, never the real `procs/`: only the lock-holder may reap."""
    return agent_runner(settings, PROJECT_SUBDIR, records=eval_dir)


def agent_runner(settings: Settings, project_subdir: str, records) -> SubprocessRunner:
    harness = harness_named(settings.harness)
    if missing := missing_capabilities(harness):
        names = ", ".join(capability.value for capability in missing)
        raise SettingsError(f"harness {settings.harness!r} lacks what every stage needs: {names}")
    return SubprocessRunner(
        settings.claude_executable, project_subdir, settings.runner_timeout_s,
        sandbox=sandbox_named(settings.sandbox, settings.sandbox_executable), records=records,
        private_dirs=settings.private_dirs, harness=harness,
    )


def _serve_with_uvicorn(app, host: str, port: int) -> None:
    import uvicorn
    # uvicorn waits for open connections before the lifespan shutdown and never tells them to stop, so the
    # signal hooks themselves must end the event streams; the lifespan would be too late
    request_stop = app.state.request_stop

    class StreamEndingServer(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            super().handle_exit(sig, frame)
            request_stop()

    def hang_up(signum, frame) -> None:
        server.should_exit = True
        request_stop()

    # the backstop cancels any other request still running 5 s into the shutdown
    server = StreamEndingServer(uvicorn.Config(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=5))
    # uvicorn handles SIGINT and SIGTERM itself; a hangup must also shut down cleanly or the job worker's agent outlives us
    signal.signal(signal.SIGHUP, hang_up)
    server.run()


@dataclass
class Context:
    settings: Settings
    store: TaskStore | SqliteTaskStore
    ledger: Ledger | SqliteLedger
    stages: dict
    clock: Callable[[], datetime] = datetime.now
    reap: Callable[[Path | SqliteProcessRecords], ReapResult] = _reap_with_the_real_process_table
    eval_runner: Callable[[Settings, Path], Runner] = _real_eval_runner
    serve: Callable[[object, str, int], None] = _serve_with_uvicorn
    server: ServerClient | None = None
    repo: str | None = None  # the repo this command runs in


@dataclass
class ServerContext:
    """What `serve` and `ui` run with: the global settings, and a loader of the repos under the home.

    `load_repos` returns the contexts to start and, for each directory that cannot be one, why not."""

    settings: GlobalSettings
    load_repos: Callable[[], tuple[dict[str, Context], dict[str, str]]]
    serve: Callable[[object, str, int], None] = _serve_with_uvicorn
    server: ServerClient | None = None
    clock: Callable[[], datetime] = datetime.now


def _print_reaped(killed, discarded, io, repo: str | None = None) -> None:
    where = f"{repo}: " if repo else ""
    for record in killed:
        print(f"{where}killed orphan of {record.task_id} ({record.stage}), pid {record.pid}", file=io.stdout)
    for record in discarded:
        print(f"{where}discarded invalid process record {record.path.name}: {record.reason}", file=io.stdout)


class _StoppedWaiting(Exception):
    """The user interrupted the wait; the job carries on in the server."""

    def __init__(self, job_id: str):
        super().__init__(job_id)
        self.job_id = job_id


NO_REPO_MESSAGE = "no repo: run inside a repo under the factory home, or pass --repo <name>"


class NoRepo(Exception):
    """The working directory is in no repo."""
