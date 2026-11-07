import argparse
import signal
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from factory.textsafe import escape_control  # noqa: F401  (re-exported)
from factory.advance import advance
from factory.approve_plan import approve_plan, reject_plan
from factory.abandon_task import AbandonRefused, abandon_task
from factory.rebase_task import RebaseRefused, rebase_task
from factory.clean_tasks import clean_candidates, clean_tasks, remove_orphan_caches
from factory.gate_cache import orphan_caches
from factory.digest import plan_digest
from factory.ids import InvalidTaskId
from factory.ledger import Ledger
from factory.lock import AlreadyRunning, state_lock
from factory.merge_task import MergeRefused, merge_preview, merge_task
from factory.retry_task import RetryRefused, retry_task
from factory.recover_task import RecoverRefused, recover_task
from factory.runner import SubprocessRunner
from factory.settings import Settings, SettingsError, load_settings
from factory.stages import Deps, build_stages
from factory.store import TaskNotFound, TaskStore, create_with_fresh_id
from factory.task import InvalidTransition, Task, TaskState
from factory.worktrees import WorktreeManager


@dataclass
class IO:
    stdin: object
    stdout: object
    stderr: object


@dataclass
class Context:
    settings: Settings
    store: TaskStore
    ledger: Ledger
    stages: dict
    clock: Callable[[], datetime] = datetime.now


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
    for name in ("show", "advance", "approve", "reject", "merge", "retry", "recover", "abandon", "rebase"):
        commands.add_parser(name).add_argument("task_id")
    commands.add_parser("clean")
    commands.add_parser("costs").add_argument("task_id", nargs="?")
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


def _load(ctx: Context, task_id: str) -> Task:
    return ctx.store.load(task_id)


def _new(args, io, ctx) -> int:
    ctx.settings.tasks_dir.mkdir(parents=True, exist_ok=True)
    task = create_with_fresh_id(
        ctx.store, ctx.clock, lambda task_id: Task(id=task_id, prompt="", state=TaskState.PROPOSED, goal=args.goal)
    )
    print(task.id, file=io.stdout)
    return 0


def _list(args, io, ctx) -> int:
    listing = ctx.store.list()
    for task in listing.tasks:
        reason = f"  {task.failure_reason}" if task.failure_reason else ""
        print(f"{task.id}  {task.state.value}{reason}", file=io.stdout)
    for name in listing.skipped:
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
    print(escape_control("\n".join(_describe(_load(ctx, args.task_id)))), file=io.stdout)
    return 0


def _advance(args, io, ctx) -> int:
    _load(ctx, args.task_id)
    try:
        with state_lock(ctx.settings.state_dir):
            result = advance(args.task_id, ctx.store, ctx.stages)
    except AlreadyRunning:
        print("another factory run holds the lock", file=io.stderr)
        return 1
    for step in result.steps:
        print(f"{step.stage} -> {step.state.value}", file=io.stdout)
    task = result.task
    print(f"state: {task.state.value}" + (f" ({task.failure_reason})" if task.failure_reason else ""), file=io.stdout)
    if result.stopped:
        print(f"stopped: {result.stopped}", file=io.stdout)
        return 1
    if task.state is TaskState.REVIEWED:
        print(f"review passed; run: factory approve {task.id}", file=io.stdout)
    return 1 if task.state is TaskState.FAILED else 0


def _not_reviewed(task: Task, io) -> int:
    print(f"{task.id} is {task.state.value}, not reviewed", file=io.stderr)
    return 1


def _reject(args, io, ctx) -> int:
    task = _load(ctx, args.task_id)
    if task.state is not TaskState.REVIEWED:
        return _not_reviewed(task, io)
    reject_plan(args.task_id, ctx.store)
    print(f"{task.id}: plan rejected", file=io.stdout)
    return 0


def _approve(args, io, ctx) -> int:
    task = _load(ctx, args.task_id)
    if not io.stdin.isatty():
        print("approval needs an interactive terminal", file=io.stderr)
        return 1
    if task.state is not TaskState.REVIEWED:
        return _not_reviewed(task, io)
    shown = "\n".join(
        [f"GOAL:\n{task.goal}", f"PLAN:\n{task.plan}", "RISKS:"] + [f"  - {r}" for r in task.plan_risks]
        + ["REVIEW FINDINGS:"] + [f"  - {f}" for f in task.review_findings]
    )
    print(escape_control(shown), file=io.stdout)
    print("Approve this plan? [y/N] ", end="", file=io.stdout)
    io.stdout.flush()
    if io.stdin.readline().strip() != "y":
        print("not approved", file=io.stdout)
        return 1
    try:
        approve_plan(task.id, ctx.store, plan_digest(task.plan))
    except ValueError:
        print("plan changed since display; nothing approved", file=io.stderr)
        return 1
    except InvalidTransition:
        return _not_reviewed(ctx.store.load(task.id), io)
    print(f"{task.id}: approved and queued; run: factory advance {task.id}", file=io.stdout)
    return 0


def _costs(args, io, ctx) -> int:
    if args.task_id is not None:
        _load(ctx, args.task_id)
    entries = [e for e in ctx.ledger.entries() if args.task_id in (None, e["task_id"])]
    by_stage, by_task = defaultdict(float), defaultdict(float)
    for entry in entries:
        by_stage[entry.get("stage", "unknown")] += entry["cost_usd"]
        by_task[entry["task_id"]] += entry["cost_usd"]
    print("by stage:", file=io.stdout)
    for stage, cost in sorted(by_stage.items()):
        print(f"  {stage}  ${cost:.4f}", file=io.stdout)
    print("by task:", file=io.stdout)
    for task_id, cost in sorted(by_task.items()):
        print(f"  {task_id}  ${cost:.4f}", file=io.stdout)
    assumed = sum(e["cost_usd"] for e in entries if e.get("assumed"))
    print(f"assumed: ${assumed:.4f}", file=io.stdout)
    print(f"total: ${sum(e['cost_usd'] for e in entries):.4f}", file=io.stdout)
    return 0


def _retry(args, io, ctx) -> int:
    _load(ctx, args.task_id)
    try:
        new = retry_task(args.task_id, ctx.store, ctx.clock)
    except RetryRefused as error:
        print(f"retry refused: {error}", file=io.stderr)
        return 1
    print(new.id, file=io.stdout)
    return 0


def _recover(args, io, ctx) -> int:
    task = _load(ctx, args.task_id)
    lock_file = ctx.settings.state_dir / "lock"
    last_driver = lock_file.read_text().strip() if lock_file.exists() else "unknown"  # read before taking the lock
    try:
        with state_lock(ctx.settings.state_dir):
            recover_task(args.task_id, ctx.store, ctx.ledger, ctx.settings.budget_usd)
    except AlreadyRunning:
        print("another factory run holds the lock", file=io.stderr)
        return 1
    except RecoverRefused as error:
        print(f"recover refused: {error}", file=io.stderr)
        return 1
    print(f"{task.id}: marked failed (interrupted); last driver pid {last_driver}, worktree {task.worktree}", file=io.stdout)
    print("The agent may still be alive if the driver was killed with SIGKILL: check before cleaning.", file=io.stdout)
    print("Its work is uncommitted in the worktree; `factory clean` would discard it.", file=io.stdout)
    return 0


def _abandon(args, io, ctx) -> int:
    _load(ctx, args.task_id)
    try:
        with state_lock(ctx.settings.state_dir):
            task = abandon_task(args.task_id, ctx.store)
    except AlreadyRunning:
        print("another factory run holds the lock", file=io.stderr)
        return 1
    except AbandonRefused as error:
        print(f"abandon refused: {error}", file=io.stderr)
        return 1
    print(f"{task.id}: abandoned; `factory clean` removes its worktree (a killed driver may have left an agent running)", file=io.stdout)
    return 0


def _rebase(args, io, ctx) -> int:
    _load(ctx, args.task_id)
    try:
        with state_lock(ctx.settings.state_dir):
            rebased = rebase_task(args.task_id, ctx.store, ctx.settings.repo)
    except AlreadyRunning:
        print("another factory run holds the lock", file=io.stderr)
        return 1
    except (RebaseRefused, InvalidTransition, subprocess.CalledProcessError) as error:
        print(escape_control(f"rebase refused: {error}"), file=io.stderr)
        return 1
    print(f"{args.task_id}: rebased onto main, back to agent_done; run `factory advance {args.task_id}` to re-verify and re-audit", file=io.stdout)
    print(f"the previously audited commit was {rebased.old_audited_sha}; it is unreachable once the branch is cleaned", file=io.stdout)
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


def _clean(args, io, ctx) -> int:
    if not io.stdin.isatty():
        print("clean needs an interactive terminal", file=io.stderr)
        return 1
    settings = ctx.settings
    cache_root = settings.gate_cache_dir
    try:
        with state_lock(settings.state_dir):
            candidates = clean_candidates(ctx.store, settings.repo, settings.worktree_root, cache_root)
            scan = orphan_caches(ctx.store, cache_root)
            if not (candidates or scan.orphans or scan.skipped):
                print("nothing to clean", file=io.stdout)
                return 0
            lines = [_describe_candidate(c) for c in candidates]
            lines += [f"orphan gate cache {o.name}: {o.size} bytes, no task record" for o in scan.orphans]
            lines += [f"skipped cache entry {name}: not a plain directory with a readable task record" for name in scan.skipped]
            print(escape_control("\n".join(lines)), file=io.stdout)
            if not (candidates or scan.orphans):
                return 1
            print("Clean these? [y/N] ", end="", file=io.stdout)
            io.stdout.flush()
            if io.stdin.readline().strip() != "y":
                print("not cleaned", file=io.stdout)
                return 1
            worktrees = WorktreeManager(settings.repo, settings.worktree_root)
            outcomes = clean_tasks(candidates, ctx.store, worktrees, settings.repo, settings.worktree_root, cache_root)
            outcomes += remove_orphan_caches(ctx.store, cache_root, scan.orphans)
    except AlreadyRunning:
        print("another factory run holds the lock", file=io.stderr)
        return 1
    for outcome in outcomes:
        for line in outcome.done + outcome.problems:
            print(escape_control(f"{outcome.task_id}: {line}"), file=io.stdout)
    return 1 if scan.skipped or any(o.problems for o in outcomes) else 0


def _merge(args, io, ctx) -> int:
    task = _load(ctx, args.task_id)
    if not io.stdin.isatty():
        print("merge needs an interactive terminal", file=io.stderr)
        return 1
    if task.state is not TaskState.AUDITED:
        print(f"{task.id} is {task.state.value}, not audited", file=io.stderr)
        return 1
    settings = ctx.settings
    try:
        with state_lock(settings.state_dir):
            preview = merge_preview(args.task_id, ctx.store, settings.repo)
            shown = "\n".join([f"MERGE {preview.task.id}: fast-forward main to {preview.task.audited_sha}",
                               f"branch: {preview.branch}", "CHANGES:", preview.diffstat, "AUDIT FINDINGS:"]
                              + [f"  - {f}" for f in preview.findings])
            print(escape_control(shown), file=io.stdout)
            print("Merge? [y/N] ", end="", file=io.stdout)
            io.stdout.flush()
            if io.stdin.readline().strip() != "y":
                print("not merged", file=io.stdout)
                return 1
            worktrees = WorktreeManager(settings.repo, settings.worktree_root)
            merged = merge_task(args.task_id, ctx.store, settings.repo, worktrees, shown_sha=preview.task.audited_sha)
    except AlreadyRunning:
        print("another factory run holds the lock", file=io.stderr)
        return 1
    except (MergeRefused, InvalidTransition, subprocess.CalledProcessError) as error:
        print(f"merge refused: {error}", file=io.stderr)
        return 1
    print(f"{merged.id}: merged; commits are unsigned: sign them before pushing", file=io.stdout)
    return 0


_COMMANDS = {
    "new": _new, "list": _list, "show": _show, "advance": _advance,
    "approve": _approve, "reject": _reject, "merge": _merge, "retry": _retry, "recover": _recover, "abandon": _abandon, "rebase": _rebase, "clean": _clean, "costs": _costs,
}


def build_context(settings: Settings) -> Context:
    settings.tasks_dir.mkdir(parents=True, exist_ok=True)
    store, ledger = TaskStore(settings.tasks_dir), Ledger(settings.ledger_path)
    runner = SubprocessRunner(
        settings.claude_executable, settings.project_subdir, settings.runner_timeout_s,
        sandbox_executable=settings.sandbox_executable,
    )
    worktrees = WorktreeManager(settings.repo, settings.worktree_root)
    stages = build_stages(settings, Deps(store, ledger, runner, worktrees))
    return Context(settings, store, ledger, stages)


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
