import argparse
import signal
import sys
from dataclasses import replace
from pathlib import Path

import httpx2

from factory.audit_compare import compare, find_previous, load_run
from factory.audit_corpus import CASES as EVAL_CASES
from factory.audit_corpus import PROJECT_SUBDIR
from factory.audit_eval import check_corpus
from factory.audit_share import build_bundle, write_bundle
from factory.cli.admin import _clone, _migrate, _serve, _watch
from factory.cli.base import IO, Context, _StoppedWaiting, agent_runner
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
    _ui,
)
from factory.client import ServerClient, ServerRefused, ServerUnreachable
from factory.gate import check_gate
from factory.ids import InvalidTaskId
from factory.init_config import init_global_config
from factory.settings import BIND_HOSTS, Settings, SettingsError, load_settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_process_records import SqliteProcessRecords
from factory.sqlite_store import SqliteTaskStore
from factory.stages import Deps, build_stages, gate_sandbox_for
from factory.store import TaskNotFound
from factory.task import Task
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
    serve.add_argument("--host", choices=BIND_HOSTS)
    ui = commands.add_parser("ui", help="Print a one-time link that logs a browser into the web UI")
    ui.add_argument("--host", help="Host name for the link: a loopback name or one in server_allowed_hosts")
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
    export = commands.add_parser("eval-export")
    export.add_argument("eval_dir", type=Path)
    export.add_argument("--out", type=Path)
    export.add_argument("--with-findings", action="store_true")
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
    if args.compare:
        if not args.compare.exists():
            return "--compare must be an eval directory or an exported run.json"
        try:
            load_run(args.compare)
        except ValueError as error:
            return f"--compare: {error}"
    return None


def _new_eval_dir(evals_root: Path, clock) -> Path:
    stamp = clock().strftime("%Y%m%dT%H%M%S")
    directory, number = evals_root / stamp, 1
    while directory.exists():
        number += 1
        directory = evals_root / f"{stamp}-{number}"
    return directory


# _check_corpus and _eval_audit stay here: tests patch factory.cli.check_gate, which only works while they read it from this namespace.
def _check_corpus(io, ctx) -> int:
    """Needs uv (and usually network): the gate's real test run over every case, no agents, in the configured gate sandbox."""
    settings = ctx.settings
    make_sandbox = gate_sandbox_for(replace(settings, project_subdir=PROJECT_SUBDIR))

    def gate(worktree, base_sha):
        confined = make_sandbox(Task(id=worktree.name, prompt="", worktree=str(worktree), base_sha=base_sha))
        return check_gate(
            worktree, base_sha, PROJECT_SUBDIR, list(confined.argv), settings.gate_timeout_s,
            env=confined.env, test_prefix=confined.prefix)
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
    try:
        lines = compare(previous, eval_dir) if previous else ["\nno earlier complete run to compare with"]
    except ValueError as error:
        lines = [f"\ncannot compare with {previous.name}: {escape_control(str(error))}"]
    for line in lines:
        print(line, file=io.stdout)
    return job["result"]["exit_code"]


def _eval_export(args, io, ctx) -> int:
    """Runs without a server or a repo: it only reads an eval directory and writes two files."""
    target = args.out or args.eval_dir
    if not args.eval_dir.is_dir():
        print(f"usage error: eval directory not found: {args.eval_dir}", file=io.stderr)
        return 2
    if not target.is_dir():
        print(f"usage error: --out must be an existing directory: {target}", file=io.stderr)
        return 2
    try:
        bundle = build_bundle(args.eval_dir, EVAL_CASES, include_findings=args.with_findings)
    except ValueError as error:
        print(f"cannot export: {escape_control(str(error))}", file=io.stderr)
        return 1
    for path in write_bundle(bundle, target):
        print(path, file=io.stdout)
    return 0


_COMMANDS = {
    "new": _new, "list": _list, "show": _show, "advance": _advance,
    "answer": _answer, "approve": _approve, "reject": _reject, "merge": _merge, "retry": _retry, "recover": _recover, "abandon": _abandon, "rebase": _rebase, "clean": _clean, "migrate": _migrate, "costs": _costs, "eval-audit": _eval_audit, "eval-export": _eval_export, "serve": _serve, "watch": _watch, "ui": _ui,
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
    runner = agent_runner(settings, settings.project_subdir, records=SqliteProcessRecords(settings.db_path))
    worktrees = WorktreeManager(settings.repo, settings.worktree_root)
    stages = build_stages(settings, Deps(store, ledger, runner, worktrees))
    return Context(settings, store, ledger, stages, server=_server_client(settings))


def _exit_on_signal(signum, frame):
    raise SystemExit(128 + signum)  # a BaseException, so the runner kills the agent tree on the way out




_NEEDS_NO_REPO = ("init", "clone", "eval-export")


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
        settings = load_settings(known.config, cwd=Path.cwd())
        for warning in settings.warnings:
            print(f"settings warning: {warning}", file=sys.stderr)
        ctx = build_context(settings)
    except SettingsError as error:
        print(f"settings error: {error}", file=sys.stderr)
        sys.exit(2)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, _exit_on_signal)
    sys.exit(main(rest, io, ctx))

