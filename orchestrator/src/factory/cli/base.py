import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from factory.audit_corpus import PROJECT_SUBDIR
from factory.client import ServerClient
from factory.failure_codes import explain
from factory.github import GitHub, origin_slug
from factory.harness import harness_named, missing_capabilities
from factory.ids import require_safe_id
from factory.ledger import Ledger
from factory.process import group_alive, kill_tree
from factory.process_registry import (
    ReapResult,
    ps_probe,
    reap_orphans,
)
from factory.runner import SubprocessRunner
from factory.sandbox import sandbox_named
from factory.settings import Settings, SettingsError
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_process_records import SqliteProcessRecords
from factory.sqlite_store import SqliteTaskStore
from factory.step import Runner
from factory.store import TaskStore
from factory.task import Task
from factory.textsafe import escape_control
from factory.waiver import (
    Refuse,
    Reinstate,
    Waive,
    approval_prompt,
    parse_answer,
)
from factory.watch import run_pass


def _coded(field: str, code: str) -> str:
    """The raw code first, so greps keep working, then our description of it; free text is shown as it is."""
    explanation = explain(field, code)
    return f"{code} - {explanation.text}" if explanation else code


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
        private_dirs=(str(settings.state_dir),), harness=harness,
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
    probe: Callable[[int], float | None] = field(default_factory=ps_probe)
    group_alive: Callable[[int], bool] = group_alive
    watch_pass: Callable[..., None] = run_pass
    github: Callable[[str, str], GitHub] = GitHub.default
    origin_slug: Callable[[Path], tuple[str, str]] = origin_slug
    sleep: Callable[[float], None] = time.sleep


def _print_reaped(killed, discarded, io) -> None:
    for record in killed:
        print(f"killed orphan of {record.task_id} ({record.stage}), pid {record.pid}", file=io.stdout)
    for record in discarded:
        print(f"discarded invalid process record {record.path.name}: {record.reason}", file=io.stdout)


class _StoppedWaiting(Exception):
    """The user interrupted the wait; the job carries on in the server."""

    def __init__(self, job_id: str):
        super().__init__(job_id)
        self.job_id = job_id


def _task_path(task_id: str, suffix: str = "") -> str:
    return f"/tasks/{require_safe_id(task_id)}{suffix}"


def _load(ctx: Context, task_id: str) -> Task:
    return ctx.store.load(task_id)


def _run_job(ctx, io, path: str, body: dict | None = None) -> dict | None:
    """Queue the job, wait for it, and return its result; None (after saying why on stderr) when it did not succeed."""
    job_id = ctx.server.post(path, body)["job"]["id"]
    try:
        job = ctx.server.wait(job_id)
    except KeyboardInterrupt:
        raise _StoppedWaiting(job_id) from None
    if job["state"] != "succeeded":
        print(escape_control(job["error"] or f"job {job['state']}"), file=io.stderr)
        return None
    return job["result"]


def _ask_with_waiver(io, question: str, confirm_question: str, required: list[str], declined: str,
                     waived_earlier: tuple[str, ...] = ()) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Ask `question`; return (notes to waive, notes to reinstate), both empty for `y`, or None when nothing should go ahead."""
    print(approval_prompt(question, required, len(waived_earlier)), end="", file=io.stdout)
    io.stdout.flush()
    answer = parse_answer(io.stdin.readline(), len(required), len(waived_earlier))
    waived: tuple[str, ...] = ()
    if isinstance(answer, Reinstate):
        reinstated = tuple(waived_earlier[number - 1] for number in answer.numbers)
        print(escape_control("\n".join(["Reinstating (these will be sent to the agents and enforced by the audit again):",
                                        *(f"  - {note}" for note in reinstated)])), file=io.stdout)
        print("Reinstate these and queue? [y/N] ", end="", file=io.stdout)
        io.stdout.flush()
        if io.stdin.readline().strip() != "y":
            print(declined, file=io.stdout)
            return None
        return (), reinstated
    if isinstance(answer, Waive):
        waived = tuple(required[number - 1] for number in answer.numbers)
        print(escape_control("\n".join(["Waiving (these will not be sent to the agents or enforced by the audit):",
                                        *(f"  - {note}" for note in waived)])), file=io.stdout)
        print(confirm_question, end="", file=io.stdout)
        io.stdout.flush()
        if io.stdin.readline().strip() != "y":
            answer = Refuse(reason=None)
    if isinstance(answer, Refuse):
        if answer.reason:
            print(escape_control(answer.reason), file=io.stderr)
        print(declined, file=io.stdout)
        return None
    return waived, ()
