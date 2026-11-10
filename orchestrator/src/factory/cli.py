import argparse
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
from factory.textsafe import escape_control  # noqa: F401  (re-exported)
from factory.advance import advance
from factory.client import ServerClient, ServerRefused, ServerUnreachable
from factory.audit_compare import compare, find_previous
from factory.audit_gaps import split_quoting_waived
from factory.audit_corpus import CASES as EVAL_CASES, PROJECT_SUBDIR
from factory.audit_eval import check_corpus
from factory.digest import plan_digest
from factory.gate import check_gate
from factory.ids import InvalidTaskId, require_safe_id
from factory.ledger import Ledger
from factory.migrate import DatabaseNotEmpty, migrate
from factory.process import group_alive, kill_tree
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.process_registry import ReapResult, ps_probe, reap_orphans
from factory.merge_task import MergeRefused  # noqa: F401  (tests raise it via cli)
from factory.runner import SubprocessRunner
from factory.settings import Settings, SettingsError, load_settings
from factory.stages import Deps, build_stages
from factory.step import Runner
from factory.store import TaskNotFound, TaskStore, task_from_dict
from factory.task import Task, TaskState
from factory.waiver import Refuse, Reinstate, Waive, approval_prompt, note_lines, parse_answer
from factory.worktrees import WorktreeManager


@dataclass
class IO:
    stdin: object
    stdout: object
    stderr: object


def _reap_with_the_real_process_table(state_dir) -> ReapResult:
    return reap_orphans(state_dir, ps_probe(), kill_tree, group_alive)


def _real_eval_runner(settings: Settings, eval_dir: Path) -> SubprocessRunner:
    """The eval records its agents under its own directory, never the real `procs/`: only the lock-holder may reap."""
    return SubprocessRunner(
        settings.claude_executable, PROJECT_SUBDIR, settings.runner_timeout_s,
        sandbox_executable=settings.sandbox_executable, state_dir=eval_dir,
    )


def _serve_with_uvicorn(app, host: str, port: int) -> None:
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    # uvicorn handles SIGINT and SIGTERM itself; a hangup must also shut down cleanly or the job worker's agent outlives us
    signal.signal(signal.SIGHUP, lambda signum, frame: setattr(server, "should_exit", True))
    server.run()


@dataclass
class Context:
    settings: Settings
    store: TaskStore
    ledger: Ledger
    stages: dict
    clock: Callable[[], datetime] = datetime.now
    reap: Callable[[Path], ReapResult] = _reap_with_the_real_process_table
    eval_runner: Callable[[Settings, Path], Runner] = _real_eval_runner
    serve: Callable[[object, str, int], None] = _serve_with_uvicorn
    server: ServerClient | None = None


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


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="factory")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    commands.add_parser("new").add_argument("goal")
    commands.add_parser("list")
    for name in ("show", "advance", "approve", "reject", "merge", "recover", "abandon", "rebase"):
        commands.add_parser(name).add_argument("task_id")
    retry = commands.add_parser("retry")
    retry.add_argument("task_id")
    retry.add_argument("--keep-plan", action="store_true")
    commands.add_parser("clean")
    commands.add_parser("migrate")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int)
    commands.add_parser("costs").add_argument("task_id", nargs="?")
    audit_eval = commands.add_parser("eval-audit")
    audit_eval.add_argument("--runs", type=int, default=3)
    audit_eval.add_argument("--only", action="append")
    audit_eval.add_argument("--max-usd", type=float, default=6.0)
    audit_eval.add_argument("--resume", type=Path)
    audit_eval.add_argument("--compare", type=Path)
    audit_eval.add_argument("--check-corpus", action="store_true")
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


def _task_path(task_id: str, suffix: str = "") -> str:
    return f"/tasks/{require_safe_id(task_id)}{suffix}"


def _load(ctx: Context, task_id: str) -> Task:
    return ctx.store.load(task_id)


def _new(args, io, ctx) -> int:
    print(ctx.server.post("/tasks", {"goal": args.goal})["id"], file=io.stdout)
    return 0


def _migrate(args, io, ctx) -> int:
    try:
        report = migrate(TaskStore(ctx.settings.tasks_dir), Ledger(ctx.settings.ledger_path), ctx.store, ctx.ledger)
    except DatabaseNotEmpty as refused:
        print(f"{refused}; nothing changed", file=io.stderr)
        return 1
    print(f"migrated {report.tasks} tasks, {report.ledger_entries} ledger entries", file=io.stdout)
    for name in report.skipped:
        print(f"skipped: {name}", file=io.stdout)
    return 0


def _list(args, io, ctx) -> int:
    listing = ctx.server.get("/tasks")
    for task in listing["tasks"]:
        reason = f"  {task['failure_reason']}" if task["failure_reason"] else ""
        print(f"{task['id']}  {task['state']}{reason}", file=io.stdout)
    for name in listing["skipped"]:
        print(f"skipped: {name}", file=io.stdout)
    return 0


def _describe(task: Task) -> list[str]:
    lines = [f"id: {task.id}", f"state: {task.state.value}", f"goal: {task.goal}"]
    if task.failure_reason:
        lines.append(f"failure: {task.failure_reason}")
    if task.retry_of:
        lines.append(f"retry_of: {task.retry_of}")
    if task.fix_rounds_used:
        lines.append(f"fix_rounds_used: {task.fix_rounds_used}")
    lines += [f"plan:\n{task.plan}"] if task.plan else []
    for label, items in (("risks", task.plan_risks), ("review findings", task.review_findings),
                         ("audit findings", task.audit_findings)):
        lines += [f"{label}:"] + [f"  - {item}" for item in items] if items else []
    for label, value in (("plan_error", task.plan_error), ("review_error", task.review_error),
                         ("audit_error", task.audit_error)):
        if value:
            lines.append(f"{label}: {value}")
    if task.worktree:
        lines += [f"worktree: {task.worktree}", f"branch: factory/{task.id}"]
    return lines


def _show(args, io, ctx) -> int:
    task = task_from_dict(ctx.server.get(_task_path(args.task_id)))
    print(escape_control("\n".join(_describe(task))), file=io.stdout)
    return 0


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


def _advance(args, io, ctx) -> int:
    result = _run_job(ctx, io, _task_path(args.task_id, "/advance"))
    if result is None:
        return 1
    for step in result["steps"]:
        print(f"{step['stage']} -> {step['state']}", file=io.stdout)
    reason = result["failure_reason"]
    print(f"state: {result['state']}" + (f" ({reason})" if reason else ""), file=io.stdout)
    if result["stopped"]:
        print(f"stopped: {result['stopped']}", file=io.stdout)
        return 1
    if result["state"] == TaskState.REVIEWED.value:
        print(f"review passed; run: factory approve {args.task_id}", file=io.stdout)
    return 1 if result["state"] == TaskState.FAILED.value else 0


def _not_reviewed(task: Task, io) -> int:
    print(f"{task.id} is {task.state.value}, not reviewed", file=io.stderr)
    return 1


def _reject(args, io, ctx) -> int:
    try:
        task = ctx.server.post(_task_path(args.task_id, "/reject"))
    except ServerRefused as refused:
        if refused.error != "wrong state":
            raise
        print(refused.detail, file=io.stderr)
        return 1
    print(f"{task['id']}: plan rejected", file=io.stdout)
    return 0


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


def _approve(args, io, ctx) -> int:
    ctx.server.get(_task_path(args.task_id))
    if not io.stdin.isatty():
        print("approval needs an interactive terminal", file=io.stderr)
        return 1
    try:
        preview = ctx.server.get(_task_path(args.task_id, "/approval"))
    except ServerRefused as refused:
        if refused.error != "wrong state":
            raise
        print(refused.detail, file=io.stderr)
        return 1
    note_display, required = note_lines(tuple(preview["review_findings"]))
    shown = "\n".join(
        [f"GOAL:\n{preview['goal']}", f"PLAN:\n{preview['plan']}", "RISKS:"] + [f"  - {r}" for r in preview["risks"]]
        + ["REVIEWER NOTES (sent to the implementer):"] + note_display
    )
    print(escape_control(shown), file=io.stdout)
    if preview["warning"]:
        print(preview["warning"], file=io.stdout)
    answered = _ask_with_waiver(io, "Approve this plan? [y/N]", "Waive these and approve? [y/N] ", required, "not approved")
    if answered is None:
        return 1
    waived, _ = answered
    try:
        ctx.server.post(_task_path(args.task_id, "/approve"), {"plan_digest": preview["plan_digest"], "waive": list(waived)})
    except ServerRefused as refused:
        if refused.error == "invalid waiver":
            print(f"cannot waive: {escape_control(refused.detail)}; nothing approved", file=io.stderr)
        elif refused.error == "plan changed":
            print("plan changed since display; nothing approved", file=io.stderr)
        elif refused.error == "wrong state":
            print(refused.detail, file=io.stderr)
        else:
            raise
        return 1
    waived_note = f" ({len(waived)} required note{'s' if len(waived) != 1 else ''} waived)" if waived else ""
    print(f"{args.task_id}: approved and queued{waived_note}; run: factory advance {args.task_id}", file=io.stdout)
    return 0


def _costs(args, io, ctx) -> int:
    report = SimpleNamespace(**ctx.server.get("/costs", **({"task_id": require_safe_id(args.task_id)} if args.task_id else {})))
    print("by stage:", file=io.stdout)
    for stage, cost in sorted(report.by_stage.items()):
        print(f"  {stage}  ${cost:.4f}", file=io.stdout)
    print("by task:", file=io.stdout)
    for task_id, cost in sorted(report.by_task.items()):
        print(f"  {task_id}  ${cost:.4f}", file=io.stdout)
    print(f"assumed: ${report.assumed:.4f}", file=io.stdout)
    print(f"total: ${report.total:.4f}", file=io.stdout)
    return 0


def _retry(args, io, ctx) -> int:
    ctx.server.get(_task_path(args.task_id))
    if args.keep_plan:
        return _retry_keeping_plan(args.task_id, io, ctx)
    try:
        new = ctx.server.post(_task_path(args.task_id, "/retry"))
    except ServerRefused as refused:
        if refused.error != "retry refused":
            raise
        print(f"retry refused: {refused.detail}", file=io.stderr)
        return 1
    print(new["id"], file=io.stdout)
    return 0


def _retry_keeping_plan(task_id: str, io, ctx) -> int:
    if not io.stdin.isatty():
        print("keeping a plan needs an interactive terminal", file=io.stderr)
        return 1
    try:
        task = ctx.server.get(_task_path(task_id, "/retry-preview"))
    except ServerRefused as refused:
        if refused.error != "retry refused":
            raise
        print(f"retry refused: {refused.detail}", file=io.stderr)
        return 1
    waived_earlier = tuple(task["waived_findings"])
    note_display, required = note_lines(tuple(task["review_findings"]))
    lines = [
        f"RETRY of {task_id} ({task['state']}), keeping its approved plan", f"GOAL:\n{task['goal']}", f"PLAN:\n{task['plan']}", "RISKS:",
        *(f"  - {r}" for r in task["risks"]), "REVIEWER NOTES (sent to the implementer):", *note_display,
    ]
    if waived_earlier:
        lines += ["WAIVED EARLIER (not sent):", *(f"  - {f}" for f in waived_earlier)]
        lines += ["REINSTATE with r N:", *(f"  - [r{i}] {f}" for i, f in enumerate(waived_earlier, 1))]
    gaps = tuple(task["audit_gaps"])
    if gaps:
        lines += ["AUDIT FINDINGS (sent to the next attempt):", *(f"  - {g}" for g in gaps)]
    if task["failure_reason"]:
        lines.append(f"FAILED BECAUSE: {task['failure_reason']}")
    if task["state"] == TaskState.AUDITED.value:
        lines += ["AUDIT FINDINGS:", *(f"  - {f}" for f in task["audit_findings"]),
                  f"{task_id} is audited and still mergeable; abandon it if you take this retry"]
    print(escape_control("\n".join(lines)), file=io.stdout)
    if task["warning"]:
        print(task["warning"], file=io.stdout)
    answered = _ask_with_waiver(
        io, f"Queue this plan again as a new task (retry of {task_id})? [y/N]", "Waive these and queue? [y/N] ", required, "not queued",
        waived_earlier)
    if answered is None:
        return 1
    waived, reinstated = answered
    for dropped in split_quoting_waived(gaps, waived)[1]:
        print(escape_control(f"dropped (quotes a waived note): {dropped}"), file=io.stdout)
    try:
        new = ctx.server.post(_task_path(task_id, "/retry"), {
            "keep_plan": True, "plan_digest": task["plan_digest"], "waive": list(waived), "reinstate": list(reinstated)})
    except ServerRefused as refused:
        if refused.error == "invalid waiver":
            action = "reinstate" if reinstated else "waive"
            print(f"cannot {action}: {escape_control(refused.detail)}; nothing queued", file=io.stderr)
        elif refused.error == "retry refused":
            print("plan changed since display; nothing queued", file=io.stderr)
        else:
            raise
        return 1
    print(f"{new['id']}: queued with the approved plan; run: factory advance {new['id']}", file=io.stdout)
    return 0


def _recover(args, io, ctx) -> int:
    task = task_from_dict(ctx.server.get(_task_path(args.task_id)))
    if _run_job(ctx, io, _task_path(args.task_id, "/recover")) is None:
        return 1
    print(f"{task.id}: marked failed (interrupted); worktree {task.worktree}", file=io.stdout)
    print("Agents the dead driver recorded were killed first; one launched in the instant before SIGKILL may be unrecorded: check before cleaning.", file=io.stdout)
    print("Its work is uncommitted in the worktree; `factory clean` would discard it.", file=io.stdout)
    return 0


def _abandon(args, io, ctx) -> int:
    if _run_job(ctx, io, _task_path(args.task_id, "/abandon")) is None:
        return 1
    print(f"{args.task_id}: abandoned; `factory clean` removes its worktree (a killed driver may have left an agent running)", file=io.stdout)
    return 0


def _rebase(args, io, ctx) -> int:
    rebased = _run_job(ctx, io, _task_path(args.task_id, "/rebase"))
    if rebased is None:
        return 1
    print(f"{args.task_id}: rebased onto main, back to agent_done; run `factory advance {args.task_id}` to re-verify and re-audit", file=io.stdout)
    print(f"the previously audited commit was {rebased['old_audited_sha']}; it is unreachable once the branch is cleaned", file=io.stdout)
    return 0


def _merged_branch_flag(candidate) -> str:
    """`clean` tries `branch -d`: it needs main on a branch, and git must agree the branch is merged."""
    if candidate.commits_ahead:
        return "branch will be deleted if git agrees it is merged"
    return "branch will be deleted if main is on a branch"


def _describe_candidate(candidate) -> str:
    flags = ["worktree present" if candidate.directory_exists else "worktree missing"]
    flags += ["dirty (uncommitted work will be discarded)"] if candidate.dirty else []
    flags += [f"{candidate.commits_ahead} commit{'s' if candidate.commits_ahead != 1 else ''} ahead of base"] if candidate.commits_ahead else []
    will_delete_branch = candidate.branch_empty and not candidate.outside_root and candidate.task.state is TaskState.FAILED  # merged ones use `-d`
    flags += ["empty branch will be deleted"] if will_delete_branch else []
    if candidate.branch_exists and not will_delete_branch:
        flags += [_merged_branch_flag(candidate) if candidate.task.state is TaskState.MERGED else "branch left behind"]
    flags += ["worktree path is not <root>/<id>: will be skipped"] if candidate.outside_root else []
    flags += [f"gate cache {candidate.cache_bytes} bytes"] if candidate.cache_bytes is not None else []
    return f"{candidate.task.id}  {candidate.task.state.value}: " + ", ".join(flags)


def _candidate_from_view(view: dict) -> SimpleNamespace:
    fields = {name: value for name, value in view.items() if name not in ("id", "state")}
    return SimpleNamespace(task=SimpleNamespace(id=view["id"], state=TaskState(view["state"])), **fields)


def _clean(args, io, ctx) -> int:
    if not io.stdin.isatty():
        print("clean needs an interactive terminal", file=io.stderr)
        return 1
    preview = ctx.server.get("/clean-preview")
    candidates = [_candidate_from_view(c) for c in preview["candidates"]]
    orphans, skipped = preview["orphans"], preview["skipped"]
    if not (candidates or orphans or skipped):
        print("nothing to clean", file=io.stdout)
        return 0
    lines = [_describe_candidate(c) for c in candidates]
    lines += [f"orphan gate cache {o['name']}: {o['size']} bytes, no task record" for o in orphans]
    lines += [f"skipped cache entry {name}: not a plain directory with a readable task record" for name in skipped]
    print(escape_control("\n".join(lines)), file=io.stdout)
    if not (candidates or orphans):
        return 1
    print("Clean these? [y/N] ", end="", file=io.stdout)
    io.stdout.flush()
    if io.stdin.readline().strip() != "y":
        print("not cleaned", file=io.stdout)
        return 1
    try:
        result = _run_job(ctx, io, "/clean", {"token": preview["token"]})
    except ServerRefused as refused:
        if refused.error != "clean refused":
            raise
        print(f"clean refused: {refused.detail}", file=io.stderr)
        return 1
    if result is None:
        return 1
    for outcome in result["outcomes"]:
        for line in outcome["done"] + outcome["problems"]:
            print(escape_control(f"{outcome['task_id']}: {line}"), file=io.stdout)
    return 1 if skipped or any(o["problems"] for o in result["outcomes"]) else 0


def _merge(args, io, ctx) -> int:
    task = task_from_dict(ctx.server.get(_task_path(args.task_id)))
    if not io.stdin.isatty():
        print("merge needs an interactive terminal", file=io.stderr)
        return 1
    if task.state is not TaskState.AUDITED:
        print(f"{task.id} is {task.state.value}, not audited", file=io.stderr)
        return 1
    try:
        preview = ctx.server.get(_task_path(args.task_id, "/merge-preview"))
    except ServerRefused as refused:
        if refused.error != "merge refused":
            raise
        print(escape_control(f"merge refused: {refused.detail}"), file=io.stderr)
        return 1
    shown = "\n".join([f"MERGE {task.id}: fast-forward main to {preview['audited_sha']}",
                       f"branch: {preview['branch']}", "CHANGES:", preview["diffstat"], "AUDIT FINDINGS:"]
                      + [f"  - {f}" for f in preview["findings"]])
    print(escape_control(shown), file=io.stdout)
    print("Merge? [y/N] ", end="", file=io.stdout)
    io.stdout.flush()
    if io.stdin.readline().strip() != "y":
        print("not merged", file=io.stdout)
        return 1
    try:
        merged = _run_job(ctx, io, _task_path(args.task_id, "/merge"), {"audited_sha": preview["audited_sha"]})
    except ServerRefused as refused:
        if refused.error != "merge refused":
            raise
        print(escape_control(f"merge refused: {refused.detail}"), file=io.stderr)
        return 1
    if merged is None:
        return 1
    print(f"{task.id}: merged; commits are unsigned: sign them before pushing", file=io.stdout)
    return 0


def _serve(args, io, ctx) -> int:
    from factory.server import StartRefused, TokenFileUnsafe, claim_state_dir, create_secured_app, load_or_create_token
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
    cases = [c for c in EVAL_CASES if not args.only or c.id in args.only]
    body = {"runs": args.runs, "max_usd": args.max_usd, **({"only": args.only} if args.only else {}),
            **({"resume": args.resume.name} if args.resume else {})}
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
    "approve": _approve, "reject": _reject, "merge": _merge, "retry": _retry, "recover": _recover, "abandon": _abandon, "rebase": _rebase, "clean": _clean, "migrate": _migrate, "costs": _costs, "eval-audit": _eval_audit, "serve": _serve,
}


def _server_client(settings: Settings) -> ServerClient:
    token_file = settings.state_dir / "token"
    token = token_file.read_text().strip() if token_file.exists() else ""  # no token means no server has ever run here
    http = httpx.Client(base_url=f"http://127.0.0.1:{settings.server_port}", timeout=30.0)
    return ServerClient(http, token)


def build_context(settings: Settings) -> Context:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    store, ledger = SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path)
    runner = SubprocessRunner(
        settings.claude_executable, settings.project_subdir, settings.runner_timeout_s,
        sandbox_executable=settings.sandbox_executable, state_dir=settings.state_dir,
    )
    worktrees = WorktreeManager(settings.repo, settings.worktree_root)
    stages = build_stages(settings, Deps(store, ledger, runner, worktrees))
    return Context(settings, store, ledger, stages, server=_server_client(settings))


def _exit_on_signal(signum, frame):
    raise SystemExit(128 + signum)  # a BaseException, so the runner kills the agent tree on the way out


def run() -> None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    known, rest = pre.parse_known_args(sys.argv[1:])
    io = IO(sys.stdin, sys.stdout, sys.stderr)
    try:
        ctx = build_context(load_settings(known.config, cwd=Path.cwd()))
    except SettingsError as error:
        print(f"settings error: {error}", file=sys.stderr)
        sys.exit(2)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, _exit_on_signal)
    sys.exit(main(rest, io, ctx))


if __name__ == "__main__":
    run()
