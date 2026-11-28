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
from factory.cli.admin import _clone, _migrate, _serve, _ui
from factory.cli.base import (
    IO,
    NO_REPO_MESSAGE,
    Context,
    NoRepo,
    ServerContext,
    _StoppedWaiting,
    agent_runner,
)
from factory.cli.base import (
    _reap_with_the_real_process_table as _reap_with_the_real_process_table,
)
from factory.client import ServerClient, ServerRefused, ServerUnreachable
from factory.gate import check_gate
from factory.ids import InvalidTaskId
from factory.init_config import init_global_config
from factory.repos import discover_repos, name_problem, repo_name_from_cwd
from factory.settings import (
    BIND_HOSTS,
    Settings,
    SettingsError,
    load_global_settings,
    load_repo_settings,
)
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


def _repo_name(text: str) -> str:
    if problem := name_problem(text):
        raise argparse.ArgumentTypeError(problem)
    return text


def _preparser() -> argparse.ArgumentParser:
    """The options every command shares and that can appear anywhere on the line."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    pre.add_argument("--repo", type=_repo_name)
    return pre


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="factory")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    commands.add_parser("migrate")
    commands.add_parser("init")
    commands.add_parser("clone").add_argument("repository")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int)
    serve.add_argument("--host", choices=BIND_HOSTS)
    ui = commands.add_parser("ui", help="Print a one-time link that logs a browser into the web UI")
    ui.add_argument("--host", help="Host name for the link: a loopback name or one in server_allowed_hosts")
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
        if "invalid choice" in str(error):
            print("task commands were removed; use the web UI: `factory ui`", file=io.stderr)
        return 2
    except SystemExit as exit_:
        return exit_.code or 0
    handler = _COMMANDS[args.command]
    try:
        return handler(args, io, ctx)
    except (TaskNotFound, InvalidTaskId) as error:
        print(f"unknown task: {error}", file=io.stderr)
        return 2
    except NoRepo as error:
        print(error, file=io.stderr)
        return 2
    except _StoppedWaiting as stopped:
        print(f"stopped waiting; job {stopped.job_id} is still running on the server", file=io.stderr)
        return 130
    except ServerRefused as refused:
        if refused.status == 401:
            print(f"the server refused the token in {ctx.settings.token_path}; restart `factory serve` or check the file", file=io.stderr)
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
    "migrate": _migrate, "eval-audit": _eval_audit, "eval-export": _eval_export, "serve": _serve, "ui": _ui,
    "init": lambda args, io, ctx: init_global_config(io),
    "clone": lambda args, io, ctx: _clone(args.repository, io),
}


def _server_client(settings: Settings) -> ServerClient:
    token_file = settings.token_path
    token = token_file.read_text().strip() if token_file.exists() else ""  # no token means no server has ever run here
    http = httpx2.Client(base_url=f"http://127.0.0.1:{settings.server_port}", timeout=30.0)
    return ServerClient(http, token)


def build_context(settings: Settings, repo: str | None = None) -> Context:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    store, ledger = SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path)
    runner = agent_runner(settings, settings.project_subdir, records=SqliteProcessRecords(settings.db_path))
    worktrees = WorktreeManager(settings.repo, settings.worktree_root)
    stages = build_stages(settings, Deps(store, ledger, runner, worktrees))
    server = _server_client(settings)
    if repo is None:
        return Context(settings, store, ledger, stages, server=server)
    return Context(settings, store, ledger, stages, server=server.for_repo(repo), repo=repo)


def _exit_on_signal(signum, frame):
    raise SystemExit(128 + signum)  # a BaseException, so the runner kills the agent tree on the way out




_NEEDS_NO_REPO = ("init", "clone", "eval-export")
_SERVER_COMMANDS = ("serve", "ui")  # they act on the factory home, not on one repo


def _repo_context(config: Path | None, repo_option: str | None) -> Context:
    home = load_global_settings().home
    name = repo_option or repo_name_from_cwd(home, Path.cwd())
    if name is None:
        raise NoRepo(NO_REPO_MESSAGE)
    if not (home / name / "repo").is_dir():
        raise SettingsError(f"unknown repo {name}: there is no checkout at {home / name / 'repo'}")
    settings = load_repo_settings(home, name, config)
    for warning in settings.warnings:
        print(f"settings warning: {warning}", file=sys.stderr)
    return build_context(settings, name)


def _load_repos(home: Path) -> tuple[dict[str, Context], dict[str, str]]:
    found = discover_repos(home)
    contexts, skipped = {}, dict(found.problems)
    for name in found.names:
        try:
            settings = load_repo_settings(home, name)
            for warning in settings.warnings:
                print(f"settings warning ({name}): {warning}", file=sys.stderr)
            contexts[name] = build_context(settings, name)
        except SettingsError as error:
            skipped[name] = f"settings error: {error}"
    return contexts, skipped


def _server_context() -> ServerContext:
    settings = load_global_settings()
    return ServerContext(settings, lambda: _load_repos(settings.home), server=_server_client(settings))


def run() -> None:
    known, rest = _preparser().parse_known_args(sys.argv[1:])
    io = IO(sys.stdin, sys.stdout, sys.stderr)
    if not rest or rest[0] not in _COMMANDS:  # argparse rejects it before any repo context or state exists
        sys.exit(main(rest, io, None))
    if (rest[:1] and rest[0] in _NEEDS_NO_REPO) or {"-h", "--help"} & set(rest):
        try:
            sys.exit(main(rest, io, None))
        except SettingsError as error:
            print(f"settings error: {error}", file=sys.stderr)
            sys.exit(2)
    try:
        if rest[:1] and rest[0] in _SERVER_COMMANDS:
            ctx = _server_context()
        else:
            ctx = _repo_context(known.config, known.repo)
    except SettingsError as error:
        print(f"settings error: {error}", file=sys.stderr)
        sys.exit(2)
    except NoRepo as error:
        print(error, file=sys.stderr)
        sys.exit(2)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, _exit_on_signal)
    sys.exit(main(rest, io, ctx))

