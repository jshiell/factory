import argparse
import signal
import sys
from pathlib import Path

import httpx2

from factory.audit_compare import compare, find_previous
from factory.audit_corpus import CASES as EVAL_CASES
from factory.audit_corpus import PROJECT_SUBDIR
from factory.audit_eval import check_corpus
from factory.cli.base import IO, Context, _print_reaped, _StoppedWaiting
from factory.cli.base import (
    _reap_with_the_real_process_table as _reap_with_the_real_process_table,
)
from factory.cli.recovery import _abandon, _clean, _merge, _rebase, _recover, _retry
from factory.cli.recovery import _describe_candidate as _describe_candidate
from factory.cli.tasks import (
    _advance,
    _answer,
    _approve,
    _costs,
    _list,
    _new,
    _reject,
    _show,
)
from factory.client import ServerClient, ServerRefused, ServerUnreachable
from factory.clone_repo import RepositoryError, clone_repository
from factory.gate import check_gate
from factory.github import GitHubError
from factory.ids import InvalidTaskId
from factory.init_config import init_global_config
from factory.ledger import Ledger
from factory.lock import AlreadyRunning, state_lock
from factory.migrate import DatabaseNotEmpty, migrate
from factory.process_registry import FileRecordsBlocked, clear_file_records
from factory.runner import SubprocessRunner
from factory.settings import Settings, SettingsError, load_settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_process_records import SqliteProcessRecords
from factory.sqlite_store import SqliteTaskStore
from factory.stages import Deps, build_stages
from factory.store import TaskNotFound, TaskStore
from factory.textsafe import escape_control  # re-exported
from factory.worktrees import WorktreeManager


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="factory")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    new = commands.add_parser("new")
    new.add_argument("goal")
    new.add_argument("--no-grill", action="store_true")
    commands.add_parser("list")
    for name in ("show", "advance", "answer", "approve", "reject", "merge", "recover", "abandon", "rebase"):
        commands.add_parser(name).add_argument("task_id")
    retry = commands.add_parser("retry")
    retry.add_argument("task_id")
    retry.add_argument("--keep-plan", action="store_true")
    commands.add_parser("clean")
    commands.add_parser("migrate")
    commands.add_parser("init")
    commands.add_parser("clone").add_argument("repository")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int)
    commands.add_parser("costs").add_argument("task_id", nargs="?")
    watch = commands.add_parser("watch")
    watch.add_argument("--interval", type=_positive_int, default=60)
    watch.add_argument("--once", action="store_true")
    watch.add_argument("--allow-public", action="store_true")
    audit_eval = commands.add_parser("eval-audit")
    audit_eval.add_argument("--runs", type=int, default=3)
    audit_eval.add_argument("--only", action="append")
    audit_eval.add_argument("--max-usd", type=float, default=6.0)
    audit_eval.add_argument("--resume", type=Path)
    audit_eval.add_argument("--compare", type=Path)
    audit_eval.add_argument("--check-corpus", action="store_true")
    audit_eval.add_argument("--stage-config", action="store_true")
    return parser


def main(argv: list[str], io: IO, ctx: Context) -> int:
    try:
        args = _parser().parse_args(argv)
    except _UsageError as error:
        print(f"usage error: {error}", file=io.stderr)
        return 2
    except SystemExit as exit_:
        return exit_.code or 0
    handler = _COMMANDS[args.command]
    try:
        return handler(args, io, ctx)
    except (TaskNotFound, InvalidTaskId) as error:
        print(f"unknown task: {error}", file=io.stderr)
        return 2
    except _StoppedWaiting as stopped:
        print(f"stopped waiting; job {stopped.job_id} is still running on the server", file=io.stderr)
        return 130
    except ServerRefused as refused:
        if refused.status == 401:
            print(f"the server refused the token in {ctx.settings.state_dir / 'token'}; restart `factory serve` or check the file", file=io.stderr)
            return 1
        if refused.error == "unknown task":
            print(f"unknown task: {refused.detail}", file=io.stderr)
            return 2
        raise
    except ServerUnreachable:
        print("no factory server is running; start one with: factory serve", file=io.stderr)
        return 1








def _migrate(args, io, ctx) -> int:
    try:
        with state_lock(ctx.settings.state_dir):
            cleared = clear_file_records(ctx.settings.state_dir, ctx.probe, ctx.group_alive)
            for path in cleared.removed:
                print(f"removed stale process record {path.name}", file=io.stdout)
            for invalid in cleared.discarded:
                print(f"removed invalid process record {invalid.path.name}", file=io.stdout)
            return _copy_old_files(io, ctx, cleared_any=bool(cleared.removed or cleared.discarded))
    except FileRecordsBlocked as blocked:
        print(f"old process records remain: {blocked}; nothing changed", file=io.stderr)
        return 1
    except AlreadyRunning:
        print("another factory run holds the lock; nothing changed", file=io.stderr)
        return 1


def _copy_old_files(io, ctx, cleared_any: bool) -> int:
    try:
        report = migrate(TaskStore(ctx.settings.tasks_dir), Ledger(ctx.settings.ledger_path), ctx.store, ctx.ledger)
    except DatabaseNotEmpty as refused:
        if cleared_any:  # the process records were the work that remained; the task copy was done earlier
            print(f"{refused}; task files not copied", file=io.stdout)
            return 0
        print(f"{refused}; nothing changed", file=io.stderr)
        return 1
    print(f"migrated {report.tasks} tasks, {report.ledger_entries} ledger entries", file=io.stdout)
    for name in report.skipped:
        print(f"skipped: {name}", file=io.stdout)
    return 0














































def _serve(args, io, ctx) -> int:
    from factory.server import (
        StartRefused,
        TokenFileUnsafe,
        claim_state_dir,
        create_secured_app,
        load_or_create_token,
    )
    port = args.port if args.port is not None else ctx.settings.server_port
    try:
        token = load_or_create_token(ctx.settings.state_dir)
        with claim_state_dir(ctx) as reaped:
            _print_reaped(reaped.killed, reaped.discarded, io)
            print(f"serving on http://127.0.0.1:{port}; token in {ctx.settings.state_dir / 'token'}", file=io.stdout)
            io.stdout.flush()
            ctx.serve(create_secured_app(ctx, token), "127.0.0.1", port)
    except (StartRefused, TokenFileUnsafe) as refused:
        print(escape_control(str(refused)), file=io.stderr)
        return 1
    return 0


def _watch(args, io, ctx) -> int:
    try:
        with state_lock(ctx.settings.state_dir, name="watch"):
            return _watch_locked(args, io, ctx)
    except AlreadyRunning:
        print("another factory watch holds the lock; nothing changed", file=io.stderr)
        return 1


def _watch_locked(args, io, ctx) -> int:
    if not ctx.settings.watch_approvers:
        print("watch needs watch_approvers: set it in the repo's factory.toml to the GitHub logins whose issues may start tasks", file=io.stderr)
        return 1
    try:
        owner, name = ctx.origin_slug(ctx.settings.repo)
    except RepositoryError as error:
        print(f"watch needs a GitHub origin: {error}", file=io.stderr)
        return 1
    try:
        github = ctx.github(owner, name)
    except ValueError as error:
        print(f"watch needs a plain GitHub origin: {escape_control(str(error))}", file=io.stderr)
        return 1
    try:
        github.self_user()
    except FileNotFoundError:
        print("watch needs the gh CLI: gh is not installed", file=io.stderr)
        return 1
    except GitHubError:
        print("watch needs a logged-in gh: run `gh auth login`", file=io.stderr)
        return 1
    try:
        is_private = github.repo_is_private()
    except GitHubError as error:
        print(f"watch cannot read the repository from GitHub: {escape_control(error.stderr.strip())}", file=io.stderr)
        return 1
    if not is_private and not args.allow_public:
        print("this repo is public: plans, failure reasons and worktree paths would become public comments; pass --allow-public to watch it anyway", file=io.stderr)
        return 1
    approver_ids = set()
    for login in ctx.settings.watch_approvers:
        try:
            approver_ids.add(github.user_id(login))
        except GitHubError:
            print(f"watch cannot resolve the approver {login!r} on GitHub: check watch_approvers", file=io.stderr)
            return 1
    try:
        while True:
            ctx.watch_pass(github, ctx.server, ctx.settings.state_dir, approver_ids, merge=ctx.settings.watch_merge, retry=ctx.settings.watch_retry)
            if args.once:
                return 0
            ctx.sleep(args.interval)
    except KeyboardInterrupt:
        print("watch stopped", file=io.stderr)
        return 0


def _eval_usage_error(args, evals_root: Path) -> str | None:
    unknown = [case_id for case_id in args.only or [] if case_id not in {c.id for c in EVAL_CASES}]
    if unknown:
        return f"unknown case: {', '.join(unknown)}"
    if args.runs <= 0:
        return "--runs must be positive"
    if args.max_usd <= 0:
        return "--max-usd must be positive"
    if args.resume and not (args.resume.resolve().parent == evals_root.resolve() and (args.resume / "meta.json").is_file()):
        return f"--resume must be an earlier eval directory under {evals_root}"
    if args.compare and not args.compare.is_dir():
        return "--compare must be an existing directory"
    return None


def _new_eval_dir(evals_root: Path, clock) -> Path:
    stamp = clock().strftime("%Y%m%dT%H%M%S")
    directory, number = evals_root / stamp, 1
    while directory.exists():
        number += 1
        directory = evals_root / f"{stamp}-{number}"
    return directory


def _check_corpus(io, ctx) -> int:
    """Needs uv (and usually network): the gate's real test run over every case, no agents."""
    settings = ctx.settings
    gate = lambda worktree, base_sha: check_gate(  # noqa: E731
        worktree, base_sha, PROJECT_SUBDIR, list(settings.test_argv), settings.gate_timeout_s)
    blocked = []
    for case, result in check_corpus(EVAL_CASES, gate):
        status = "ok" if result.passed else f"BLOCKED {result.reason}"
        print(f"{case.id} {case.label:4} {case.category:24} gate={status}", file=io.stdout)
        if not result.passed:
            blocked.append(case.id)
            if result.output:
                print("    " + escape_control(result.output.strip()).replace("\n", "\n    "), file=io.stdout)
    print(f"\n{len(EVAL_CASES)} cases, " + (f"blocked: {', '.join(blocked)}" if blocked else "none blocked"), file=io.stdout)
    return 1 if blocked else 0


def _eval_audit(args, io, ctx) -> int:
    evals_root = ctx.settings.state_dir / "evals"
    problem = _eval_usage_error(args, evals_root)
    if problem:
        print(f"usage error: {problem}", file=io.stderr)
        return 2
    if args.check_corpus:
        return _check_corpus(io, ctx)
    body = {"runs": args.runs, "max_usd": args.max_usd, **({"only": args.only} if args.only else {}),
            **({"resume": args.resume.name} if args.resume else {}), **({"stage_config": True} if args.stage_config else {})}
    queued = ctx.server.post("/eval-audit", body)
    eval_dir = Path(queued["eval_dir"])
    print(f"eval directory: {eval_dir}", file=io.stdout)
    try:
        job = ctx.server.wait(queued["job"]["id"])
    except KeyboardInterrupt:
        raise _StoppedWaiting(queued["job"]["id"]) from None
    if job["state"] != "succeeded":
        print(escape_control(job["error"] or f"job {job['state']}"), file=io.stderr)
        return 1
    for line in job["result"]["report"]:
        print(line, file=io.stdout)
    previous = args.compare or find_previous(evals_root, eval_dir, [c.id for c in EVAL_CASES])
    for line in compare(previous, eval_dir) if previous else ["\nno earlier complete run to compare with"]:
        print(line, file=io.stdout)
    return job["result"]["exit_code"]


_COMMANDS = {
    "new": _new, "list": _list, "show": _show, "advance": _advance,
    "answer": _answer, "approve": _approve, "reject": _reject, "merge": _merge, "retry": _retry, "recover": _recover, "abandon": _abandon, "rebase": _rebase, "clean": _clean, "migrate": _migrate, "costs": _costs, "eval-audit": _eval_audit, "serve": _serve, "watch": _watch,
    "init": lambda args, io, ctx: init_global_config(io),
    "clone": lambda args, io, ctx: _clone(args.repository, io),
}


def _server_client(settings: Settings) -> ServerClient:
    token_file = settings.state_dir / "token"
    token = token_file.read_text().strip() if token_file.exists() else ""  # no token means no server has ever run here
    http = httpx2.Client(base_url=f"http://127.0.0.1:{settings.server_port}", timeout=30.0)
    return ServerClient(http, token)


def build_context(settings: Settings) -> Context:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    store, ledger = SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path)
    runner = SubprocessRunner(
        settings.claude_executable, settings.project_subdir, settings.runner_timeout_s,
        sandbox_executable=settings.sandbox_executable, records=SqliteProcessRecords(settings.db_path),
        private_dirs=(str(settings.state_dir),),
    )
    worktrees = WorktreeManager(settings.repo, settings.worktree_root)
    stages = build_stages(settings, Deps(store, ledger, runner, worktrees))
    return Context(settings, store, ledger, stages, server=_server_client(settings))


def _exit_on_signal(signum, frame):
    raise SystemExit(128 + signum)  # a BaseException, so the runner kills the agent tree on the way out


def _clone(spec: str, io: IO) -> int:
    try:
        return clone_repository(spec, io)
    except RepositoryError as error:
        print(error, file=io.stderr)
        return 1


_NEEDS_NO_REPO = ("init", "clone")


def run() -> None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    known, rest = pre.parse_known_args(sys.argv[1:])
    io = IO(sys.stdin, sys.stdout, sys.stderr)
    if rest[:1] and rest[0] in _NEEDS_NO_REPO:
        try:
            sys.exit(main(rest, io, None))
        except SettingsError as error:
            print(f"settings error: {error}", file=sys.stderr)
            sys.exit(2)
    try:
        ctx = build_context(load_settings(known.config, cwd=Path.cwd()))
    except SettingsError as error:
        print(f"settings error: {error}", file=sys.stderr)
        sys.exit(2)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, _exit_on_signal)
    sys.exit(main(rest, io, ctx))

